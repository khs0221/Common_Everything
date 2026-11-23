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
import shutil
import socket
import sys
import tempfile
import time
import traceback
from pathlib import Path

from . import __version__, models, offline, output, paths, pipeline, sysinfo

EXPECTED_WORDS = ("finance", "workshop", "Thursday", "budget")


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

        mdir = paths.models_dir()
        missing = models.missing_models(mdir)
        _check(results, "모델 파일", not missing, f"{mdir} 없음: {missing}" if missing else str(mdir))
        _check(results, "모델 경로(C++ 라이브러리용)", paths.path_is_safe_for_native(mdir),
               paths.native_path(mdir))

        try:
            import tkinter  # noqa: F401

            _check(results, "화면(tkinter) 포함", True)
        except Exception as e:
            _check(results, "화면(tkinter) 포함", False, repr(e))

        if os.name == "nt":
            try:
                import soundcard  # noqa: F401

                _check(results, "soundcard 포함", True)
                try:
                    n_mic = len(soundcard.all_microphones())
                    n_spk = len(soundcard.all_speakers())
                    results.append({"name": "장치 수(참고, CI 에는 없을 수 있음)", "ok": True,
                                    "detail": f"마이크 {n_mic}, 스피커 {n_spk}"})
                except Exception as e:
                    results.append({"name": "장치 목록(참고)", "ok": True, "detail": repr(e)})
            except Exception as e:
                _check(results, "soundcard 포함", False, repr(e))

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
    except Exception:
        _check(results, "예외 없음", False, traceback.format_exc()[-2000:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    report["peak_memory_mb"] = round(sysinfo.peak_memory_mb() or -1, 0)
    report["total_seconds"] = round(time.monotonic() - t0, 1)
    report["ok"] = all(c["ok"] for c in results)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
    if sys.stdout is not None:
        try:
            print(text)
        except Exception:
            pass
    return 0 if report["ok"] else 1
