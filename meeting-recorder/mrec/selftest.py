"""`MeetingRecorder.exe --selftest`: 장치 없이 프로그램이 제대로 묶였는지 확인합니다.

1. 외부 접속 차단이 켜졌는지
2. 모델 파일이 있는지, 경로가 C++ 라이브러리에 안전한지
3. 녹음 계층(가짜 장치)으로 두 트랙 WAV 쓰기
4. 동봉 표본 음성(합성 3인 대화)으로 글 옮기기·발언자 구분·결과물 생성
5. 결과 확인: 참석자 2명 + 나, 주요 단어가 나왔는지
결과는 --selftest-out 파일(JSON)과 화면(콘솔이 있으면)에 씁니다.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import sys
import tempfile
import time
import traceback
from pathlib import Path

from . import __version__, models, offline, output, paths, pipeline, sysinfo

EXPECTED_WORDS = ("finance", "workshop", "Thursday", "budget")
_HANGUL = re.compile("[\uac00-\ud7a3]")
_LATIN = re.compile("[A-Za-z]")


def text_script(text: str) -> str:
    """글자 종류로 본 언어: 한글이 더 많으면 'ko', 로마자가 있으면 'en', 없으면 '?'."""
    h, l = len(_HANGUL.findall(text)), len(_LATIN.findall(text))
    return "ko" if h > l else ("en" if l else "?")


def score_languages(truth: list[dict], utts: list) -> dict:
    """정답 발언마다 겹치는 출력 줄의 글자 종류가 정답 언어와 같은지 셉니다."""
    rows = []
    for t in truth:
        text = " ".join(u.text for u in utts if min(u.end, t["end"]) - max(u.start, t["start"]) > 0.3)
        rows.append((t["lang"], text_script(text)))
    return {"n": len(rows), "ok": sum(a == b for a, b in rows),
            "en_as_hangul": sum(a == "en" and b == "ko" for a, b in rows),
            "ko_as_latin": sum(a == "ko" and b == "en" for a, b in rows),
            "missing": sum(b == "?" for _, b in rows)}


def _check(results: list, name: str, ok: bool, detail: str = "") -> bool:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    return ok


def _fake_recording(tmp: Path) -> tuple[bool, str]:
    from .audio_io import wav_info
    from .devices import FakeBackend, FakeSource, tone
    from .recorder import Recorder

    backend = FakeBackend(mic=lambda: FakeSource(16000, tone(440, 0.2, 16000)),
                          loop=lambda: FakeSource(16000, tone(660, 0.2, 16000), gaps=[(0.3, 0.9)]))
    rec = Recorder(backend)
    folder = rec.start(tmp, None, None)
    time.sleep(1.5)
    res = rec.stop()
    sr1, _, n1 = wav_info(res.mic_path)
    sr2, _, n2 = wav_info(res.loop_path)
    ok = sr1 == sr2 == 16000 and abs(n1 - n2) <= 1 and n1 >= 16000 and folder.is_dir()
    return ok, f"mic={n1} loop={n2} frames, padded(loop)={res.stats['loop']['frames_padded']}"


def _exc_detail(e: BaseException, lines: int = 8) -> str:
    """예외 종류·메시지·traceback 마지막 몇 줄(기술 정보만)."""
    tb = traceback.format_exception(type(e), e, e.__traceback__)
    tail = "".join(tb).strip().splitlines()[-lines:]
    return f"{type(e).__name__}: {e} | " + " / ".join(x.strip() for x in tail)


def gui_device_path(results: list) -> None:
    """화면이 쓰는 것과 똑같은 경로로 장치 계층을 엽니다(R2 재현용).

    Tk 루트 생성 → default_backend()(별도 스레드에서 soundcard 불러오기) → 장치 목록 →
    기본 장치 id(녹음 스레드 방식: COM 초기화한 스레드에서 조회).
    불러오기·초기화 실패는 실패, 장치 0개는 참고(CI 러너에는 소리 장치가 없음).
    이 검사는 이 프로세스에서 soundcard 를 처음 불러올 때 해야 화면과 같은 조건이므로,
    다른 soundcard 검사보다 먼저 부릅니다.
    """
    pre = "soundcard" in sys.modules
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # gui.main 과 같게
        except Exception:
            pass
    root = None
    try:
        import tkinter as tk

        existing = getattr(tk, "_default_root", None)
        if existing is not None:  # 시험 프로세스 등 이미 Tk 루트가 있으면 그것을 씀(Tk 는 프로세스당 하나)
            _check(results, "화면 경로: Tk 루트 생성", True, "이미 있는 Tk 루트 사용")
        else:
            root = tk.Tk()
            root.withdraw()
            _check(results, "화면 경로: Tk 루트 생성", True)
    except Exception as e:
        _check(results, "화면 경로: Tk 루트 생성", False, _exc_detail(e))
    try:
        from .devices import _run_in_mta, default_backend

        try:
            backend = default_backend()
            _check(results, "화면 경로: 장치 계층 열기(default_backend)", True,
                   f"{type(backend).__name__}, 검사 전 soundcard 불러옴={pre}")
        except BaseException as e:
            _check(results, "화면 경로: 장치 계층 열기(default_backend)", False,
                   f"검사 전 soundcard 불러옴={pre} | {_exc_detail(e)}")
            return
        n = {}
        for kind, fn in (("마이크", backend.list_microphones), ("스피커", backend.list_speakers)):
            try:
                devs = fn()
                n[kind] = len(devs)
                _check(results, f"화면 경로: {kind} 목록 읽기", True,
                       f"{len(devs)}개, 기본 표시 {sum(d.is_default for d in devs)}개"
                       + (" (0개: 참고, CI 에는 장치 없음)" if not devs else ""))
            except BaseException as e:
                n[kind] = None
                _check(results, f"화면 경로: {kind} 목록 읽기", False, _exc_detail(e))
        for kind, fn in (("마이크", backend.default_microphone_id), ("스피커", backend.default_speaker_id)):
            try:
                did = _run_in_mta(fn)
                _check(results, f"화면 경로: 기본 {kind} id(녹음 스레드 방식)", True, "있음" if did else "없음")
            except BaseException as e:
                if n.get(kind) == 0:  # 장치가 없으면 기본 장치도 없음: 참고
                    results.append({"name": f"화면 경로: 기본 {kind} id(참고: 장치 0개)", "ok": True,
                                    "detail": _exc_detail(e, 4)})
                else:
                    _check(results, f"화면 경로: 기본 {kind} id(녹음 스레드 방식)", False, _exc_detail(e))
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def run(out_path: str | None = None) -> int:
    results: list[dict] = []
    report: dict = {"version": __version__, "python": sys.version.split()[0], "platform": platform.platform(),
                    "frozen": bool(getattr(sys, "frozen", False)), "checks": results}
    t0 = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="mrec_selftest_"))
    try:
        offline.enable()
        blocked = False
        try:
            socket.create_connection(("example.com", 443), timeout=2)
        except offline.NetworkBlockedError:
            blocked = True
        except OSError:
            blocked = False
        _check(results, "외부 접속 차단", blocked)

        if os.name == "nt":
            gui_device_path(results)  # soundcard 를 처음 불러오는 검사라 맨 앞에 둠
        else:
            results.append({"name": "화면 경로 장치 검사(참고)", "ok": True, "detail": "윈도우 전용이라 건너뜀"})

        mdir = paths.models_dir()
        missing = models.missing_models(mdir)
        _check(results, "모델 파일", not missing, f"{mdir} 없음: {missing}" if missing else str(mdir))
        _check(results, "모델 경로(C++ 라이브러리용)", paths.path_is_safe_for_native(mdir),
               paths.native_path(mdir))

        # exe 에서는 화면 부품이 빠졌는지 확인(필수). 개발 환경(파이썬 직접 실행)에서는 tkinter 가
        # 없을 수 있어 참고로만 적습니다(시험 환경마다 결과가 달라지지 않게, 감사 F10).
        try:
            import tkinter  # noqa: F401

            _check(results, "화면(tkinter) 포함", True)
        except Exception as e:
            if getattr(sys, "frozen", False):
                _check(results, "화면(tkinter) 포함", False, repr(e))
            else:
                results.append({"name": "화면(tkinter) 포함(참고: 개발 환경)", "ok": True,
                                "detail": f"tkinter 없음: {e!r}"})

        if os.name == "nt":
            # 묶음에 빠졌는지(ImportError)만 실패로 봅니다. CI 컴퓨터에는 소리 장치가 없어
            # 장치 관련 오류가 날 수 있는데, 그것은 참고로만 적습니다.
            try:
                import soundcard

                _check(results, "soundcard 포함", True)
                try:
                    n_mic = len(soundcard.all_microphones())
                    n_spk = len(soundcard.all_speakers())
                    results.append({"name": "장치 수(참고, CI 에는 없을 수 있음)", "ok": True,
                                    "detail": f"마이크 {n_mic}, 스피커 {n_spk}"})
                except Exception as e:
                    results.append({"name": "장치 목록(참고)", "ok": True, "detail": repr(e)})
            except ImportError as e:
                _check(results, "soundcard 포함", False, repr(e))
            except Exception as e:
                results.append({"name": "soundcard 불러오기(참고: 장치 없는 환경)", "ok": True, "detail": repr(e)})

        try:
            ok, detail = _fake_recording(tmp / "rec")
            _check(results, "가짜 장치 두 트랙 녹음", ok, detail)
        except Exception as e:
            _check(results, "가짜 장치 두 트랙 녹음", False, repr(e))

        if not missing:
            sdir = paths.samples_dir()
            folder = tmp / "회의_표본"
            folder.mkdir()
            shutil.copy(sdir / "sample_mic.flac", folder / "mic.flac")
            shutil.copy(sdir / "sample_loop.flac", folder / "loop.flac")
            job = pipeline.Job(folder, folder / "mic.flac", folder / "loop.flac", language=None)
            t = time.monotonic()
            res = pipeline.run(job)
            report["timings"] = {k: round(v, 2) for k, v in res.timings.items()}
            report["audio_seconds"] = round(res.audio_seconds, 1)
            report["processing_seconds"] = round(time.monotonic() - t, 1)
            md = (folder / output.RECORD_MD).read_text(encoding="utf-8-sig")
            txt = (folder / output.COPILOT_TXT).read_text(encoding="utf-8-sig")
            _check(results, "기록.md·Copilot용.txt 생성", bool(md) and bool(txt))
            utts, _, _ = output.load_result_json(folder)
            n_me = sum(1 for u in utts if u.speaker == "me")
            _check(results, "발언자: 참석자 2명", res.speakers == ["spk1", "spk2"], str(res.speakers))
            _check(results, "발언자: 나 4번 이상", n_me >= 4, str(n_me))
            low = txt.lower()
            found = [w for w in EXPECTED_WORDS if w.lower() in low]
            _check(results, "주요 단어 인식", len(found) >= 3, f"{found}")
            # 이름 바꾸기
            output.apply_names(folder, {"spk1": "Alice", "me": "Kim"})
            txt2 = (folder / output.COPILOT_TXT).read_text(encoding="utf-8-sig")
            _check(results, "참석자 이름 반영", "Alice:" in txt2 and "Kim:" in txt2)
            report["sample_record_md"] = md  # 합성 표본이라 회의 내용이 아닙니다

            # 혼합 언어: 한 트랙 안에서 한국어 → 영어 → 한국어로 바뀌어도 영어는 영어로, 한국어는 한글로(R3)
            mix = tmp / "회의_혼합언어"
            mix.mkdir()
            shutil.copy(sdir / "sample_mixed_lang.flac", mix / "mix.flac")
            t = time.monotonic()
            pipeline.run(pipeline.Job(mix, mix / "mix.flac", None, language=None))
            report["mixed_lang_seconds"] = round(time.monotonic() - t, 1)
            mtruth = json.loads((sdir / "sample_mixed_lang_truth.json").read_text(encoding="utf-8"))
            sc = score_languages(mtruth, output.load_result_json(mix)[0])
            report["mixed_lang_score"] = sc
            _check(results, "혼합 언어(한↔영 전환) 발언별 언어", sc["ok"] == sc["n"] and sc["en_as_hangul"] == 0,
                   f"{sc}")
    except Exception:
        _check(results, "예외 없음", False, traceback.format_exc()[-2000:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    report["peak_memory_mb"] = round(sysinfo.peak_memory_mb() or -1, 0)
    report["total_seconds"] = round(time.monotonic() - t0, 1)
    report["ok"] = all(c["ok"] for c in results)
    # 실패 요약을 맨 끝에 둡니다(CI 로그 꼬리에서 바로 보이게).
    report["failed_summary"] = failed_summary(results)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
    if sys.stdout is not None:
        try:
            print(text)
            if report["failed_summary"]:
                print("=== selftest 실패 항목 ===")
                for line in report["failed_summary"]:
                    print(line)
        except Exception:
            pass
    return 0 if report["ok"] else 1


def failed_summary(results: list, width: int = 400) -> list[str]:
    """실패한 항목의 '이름: detail(앞부분)' 목록."""
    out = []
    for c in results:
        if not c["ok"]:
            d = " ".join(str(c.get("detail", "")).split())
            out.append(f"{c['name']}: {d[:width]}")
    return out
