"""녹음 파일 → 기록.md / Copilot용.txt 처리 순서.

메모리를 아끼려고 화자 분리(스피커 트랙 전체를 메모리에 올림)를 먼저 끝내고 비운 뒤,
whisper 모델을 올려 말소리 창 단위로 글로 옮깁니다.
"""
from __future__ import annotations

import gc
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import merge, output
from .audio_io import TARGET_SR, prepare_16k_mono, slice_to_16k_mono, wav_info
from .recorder import INFO_FILE, LOOP_FILE, MIC_FILE

log = logging.getLogger("mrec")

# 단계별 진행률 비중(합 1.0)
STEPS = [("준비", 0.02), ("말소리 찾기", 0.04), ("발언자 구분", 0.10), ("언어 판별", 0.04), ("모델 읽기", 0.02),
         ("나(마이크) 글로 옮기기", 0.33), ("상대방(스피커) 글로 옮기기", 0.43), ("결과 쓰기", 0.02)]
LID_MODEL = "small"  # 발언 단위 언어 판별 전용(R6 수정 1회차: base 는 짧은 영어를 한국어로 판별하는 경우가 있어 small 유지)
# 확신 낮은 단어 (?) 표시: 측정에서 표시 정확도(정밀도) 0.5 이하·틀린 단어 중 표시 비율 0.12~0.32 로 쓸모가
# 작아 기본으로 끕니다(화면에도 두지 않음). 기능은 남겨 두어 처리결과.json 의 marked 로 다시 쓸 수 있습니다.
MARK_LOW_CONF_DEFAULT = False

ProgressCb = Callable[[str, float], None]  # (단계 이름, 전체 0~1)


@dataclass
class Job:
    folder: Path                    # 결과를 쓸 회의 폴더
    mic: Path | None                # 마이크 트랙(없으면 None: 파일 하나 처리)
    loop: Path | None               # 스피커 트랙(또는 처리할 파일 하나)
    language: str | None = None     # None=자동, 'en', 'ko'
    num_speakers: int | None = None  # 스피커 쪽 참석자 수(None=자동)
    beam_size: int = 1
    meta: dict = field(default_factory=dict)
    model: str = "turbo"            # 글로 옮기는 모델: large-v3-turbo(int8 저장)
    lid_model: str | None = LID_MODEL  # 언어 판별 모델(None 이면 model 과 같음)
    hints: bool = True              # 용어 힌트(glossary/hint_terms.txt) 사용
    mark_low_conf: bool = MARK_LOW_CONF_DEFAULT  # 대화록.txt·Copilot용.txt 에 확신 낮은 단어 (?) 표시
    guard: bool = True              # 소리가 거의 없는 30초 창의 글 거르기(설정 "tail_text_guard")
    recheck_unsure: bool = False    # 불확실 짧은 조각 다시 보기(설정 "recheck_unsure", 기본 끔: changes.md 되돌림)
    extra_terms: list[str] = field(default_factory=list)  # GUI '이번 회의 용어'(인식 힌트·회의별 교정 최상단)
    mic_min_db: float | None = -40.0  # 마이크 최소 음량 컷(dBFS, None=끄기)
    pre_transcribed: object | None = None  # 녹음 중 백그라운드 미리 전사 결과(PreTranscribed)


@dataclass
class JobResult:
    outputs: dict
    utterances: int
    speakers: list[str]
    timings: dict
    audio_seconds: float
    dropped_text: int = 0  # 소리가 거의 없는 창이라 뺀 글 수(화면 요약용)


def done_summary(res: JobResult) -> str:
    """처리 결과 요약(화면). 소리가 거의 없는 창에서 뺀 글이 있으면 건수를 함께 보입니다(감사 R6 G2)."""
    text = f"끝났습니다. 발언 {res.utterances}개, 상대방 {len(res.speakers)}명으로 구분했습니다."
    if res.dropped_text:
        text += f" 말소리 없는 구간의 글 {res.dropped_text}건 제외."
    return text


class _Progress:
    def __init__(self, cb: ProgressCb | None):
        self.cb = cb
        self.base = 0.0
        self.idx = -1

    def step(self, i: int):
        self.base = sum(w for _, w in STEPS[:i])
        self.idx = i
        self.part(0.0)

    def part(self, frac: float):
        if self.cb:
            name, w = STEPS[self.idx]
            self.cb(name, min(1.0, self.base + w * max(0.0, min(1.0, frac))))


def job_for_folder(folder: Path, language: str | None, num_speakers: int | None) -> Job:
    """녹음 폴더(마이크_나.wav, 스피커_상대방.wav)를 다시 처리하는 작업."""
    mic, loop = folder / MIC_FILE, folder / LOOP_FILE
    meta = {}
    info = folder / INFO_FILE
    if info.is_file():
        try:
            d = json.loads(info.read_text(encoding="utf-8"))
            meta = {"started_at": d.get("started_at"), "seconds": d.get("seconds")}
        except ValueError:
            pass
    return Job(folder, mic if mic.is_file() else None, loop if loop.is_file() else None,
               language, num_speakers, meta=meta)


def _shift_seg(seg, offset: float):
    from .transcribe import Segment, Word

    if offset == 0.0:
        return seg
    ws = [Word(w.start + offset, w.end + offset, w.text, w.prob) for w in seg.words]
    return Segment(seg.start + offset, seg.end + offset, seg.text, seg.lang, ws)


def run(job: Job, progress: ProgressCb | None = None, cancel: Callable[[], bool] | None = None) -> JobResult:
    from . import diarize, vad
    from .transcribe import Transcriber

    if job.mic is None and job.loop is None:
        raise FileNotFoundError("처리할 녹음 파일이 없습니다")
    pr = _Progress(progress)
    t_all = time.monotonic()
    timings: dict[str, float] = {}
    work = job.folder / "_작업중"
    work.mkdir(exist_ok=True)
    try:
        # 1) 16kHz 모노로 준비
        pr.step(0)
        t = time.monotonic()
        mic16 = prepare_16k_mono(job.mic, work / "mic16k.wav") if job.mic else None
        pr.part(0.5)
        loop16 = prepare_16k_mono(job.loop, work / "loop16k.wav") if job.loop else None
        timings["prepare"] = time.monotonic() - t
        track_durs = {
            "mic": wav_info(mic16)[2] / TARGET_SR if mic16 else 0.0,
            "loop": wav_info(loop16)[2] / TARGET_SR if loop16 else 0.0,
        }
        lengths = [d for d in track_durs.values() if d > 0]
        audio_seconds = max(lengths) if lengths else 0.0

        # 녹음 중 미리 전사된 결과가 현재 설정과 일치하면 재사용하고 남은 꼬리 구간만 처리합니다.
        pre = job.pre_transcribed
        if (pre is not None and getattr(pre, "language", None) == job.language
                and getattr(pre, "model", None) == job.model
                and getattr(pre, "mic_min_db", None) == job.mic_min_db):
            pre_tracks = getattr(pre, "tracks", {}) or {}
        else:
            pre_tracks = {}

        # 2) 말소리 구간 (미리 처리된 트랙은 남은 꼬리 구간만 VAD)
        pr.step(1)
        t = time.monotonic()
        gains = {}
        asr_wav: dict[str, Path | None] = {"mic": mic16, "loop": loop16}
        asr_segs: dict[str, list[tuple[float, float]]] = {"mic": [], "loop": []}
        asr_offset: dict[str, float] = {"mic": 0.0, "loop": 0.0}
        all_segs: dict[str, list[tuple[float, float]]] = {"mic": [], "loop": []}

        for idx_k, (key, wav16, min_db) in enumerate((("mic", mic16, job.mic_min_db), ("loop", loop16, None))):
            if not wav16:
                continue
            tp = pre_tracks.get(key)
            done_to = float(tp.done_up_to) if (tp is not None and getattr(tp, "done_up_to", 0.0) > 0.0) else 0.0
            if done_to > 0.0:
                asr_offset[key] = done_to
                if track_durs[key] - done_to >= 0.2:
                    tail_wav = slice_to_16k_mono(wav16, work / f"{key}_tail16k.wav", done_to, track_durs[key])
                    asr_wav[key] = tail_wav
                    t_segs = vad.speech_segments(tail_wav, lambda f, i=idx_k: pr.part(i * 0.5 + f * 0.5),
                                                 cancel=cancel, min_db=min_db)
                    gains[key] = round(getattr(vad.speech_segments, "last_gain", 1.0), 2)
                else:
                    asr_wav[key] = None
                    t_segs = []
                asr_segs[key] = t_segs
                all_segs[key] = list(tp.speech) + [(a + done_to, b + done_to) for a, b in t_segs]
            else:
                segs = vad.speech_segments(wav16, lambda f, i=idx_k: pr.part(i * 0.5 + f * 0.5),
                                           cancel=cancel, min_db=min_db)
                gains[key] = round(getattr(vad.speech_segments, "last_gain", 1.0), 2)
                asr_segs[key] = segs
                all_segs[key] = segs

        if any(g > 1.0 for g in gains.values()):
            log.info("말소리 찾기 음량 보정 배율: %s", gains)
        timings["vad"] = time.monotonic() - t

        # 3) 발언자 구분(스피커 트랙). 1:1 회의(상대방 1명)는 화자 분리를 생략하고, 다자 회의는 말소리 구간만 패킹해 처리합니다.
        pr.step(2)
        t = time.monotonic()
        turns = []
        if loop16 and all_segs["loop"]:
            if job.num_speakers == 1:
                log.info("상대방 1명(1:1 회의): 화자 분리 생략")
                pr.part(1.0)
            else:
                turns = diarize.diarize_file(loop16, job.num_speakers, progress=pr.part, cancel=cancel,
                                             speech=all_segs["loop"])
        gc.collect()
        timings["diarize"] = time.monotonic() - t

        # 4) 언어 판별(자동일 때) → 글로 옮기기. 두 모델을 동시에 메모리에 올리지 않습니다.
        mic_out = list(pre_tracks["mic"].segments) if ("mic" in pre_tracks and pre_tracks["mic"].done_up_to > 0) else []
        loop_out = list(pre_tracks["loop"].segments) if ("loop" in pre_tracks and pre_tracks["loop"].done_up_to > 0) else []
        dropped: dict = {
            "mic": list(pre_tracks["mic"].dropped) if ("mic" in pre_tracks and pre_tracks["mic"].done_up_to > 0) else [],
            "loop": list(pre_tracks["loop"].dropped) if ("loop" in pre_tracks and pre_tracks["loop"].done_up_to > 0) else [],
        }
        lang_stats: dict = {}
        hint = {}
        if asr_segs["mic"] or asr_segs["loop"]:  # 남은 말소리가 없으면 모델을 읽지 않습니다
            from . import glossary

            lid_name = job.lid_model or job.model
            runs = {"mic": None, "loop": None}
            unsure = {"mic": None, "loop": None}
            pr.step(3)
            t = time.monotonic()
            tr = None
            if job.language is None:
                lid = Transcriber(beam_size=job.beam_size, model=lid_name)
                for key, w0 in (("mic", 0.0), ("loop", 0.5)):
                    wav, segs = asr_wav[key], asr_segs[key]
                    if wav and segs:
                        runs[key] = lid.detect_language_runs(wav, segs, cancel=cancel,
                                                             progress=lambda f, w0=w0: pr.part(w0 + f * 0.5))
                        lang_stats[key] = lid.last_lang_stats
                        unsure[key] = lid.last_unsure
                if lid_name == job.model:
                    tr = lid  # 같은 모델이면 다시 읽지 않음
                else:
                    del lid
                    gc.collect()
            timings["lid"] = time.monotonic() - t
            pr.step(4)
            t = time.monotonic()
            hint = glossary.load_hints(extra_terms=job.extra_terms) if (job.hints or job.extra_terms) else {}
            if tr is None:
                tr = Transcriber(beam_size=job.beam_size, model=job.model, hotwords=hint.get("hotwords"),
                                 hint_vocab_text=hint.get("terms_text"))
            else:
                tr.hotwords = hint.get("hotwords")
                tr.hint_vocab_text = hint.get("terms_text")
            tr.guard = bool(job.guard)
            tr.recheck = bool(job.recheck_unsure)
            timings["load_asr"] = time.monotonic() - t
            pr.step(5)
            t = time.monotonic()
            if asr_wav["mic"] and asr_segs["mic"]:
                off = asr_offset["mic"]
                tail_mic = tr.transcribe_file(asr_wav["mic"], asr_segs["mic"], job.language, words=True,
                                              progress=pr.part, cancel=cancel, runs=runs["mic"],
                                              recheck=unsure["mic"])
                mic_out.extend(_shift_seg(s, off) for s in tail_mic)
                dropped["mic"].extend(round(t0 + off, 1) for t0, _ in tr.dropped)
                tr.dropped = []
            timings["asr_mic"] = time.monotonic() - t
            pr.step(6)
            t = time.monotonic()
            if asr_wav["loop"] and asr_segs["loop"]:
                off = asr_offset["loop"]
                tail_loop = tr.transcribe_file(asr_wav["loop"], asr_segs["loop"], job.language, words=True,
                                               progress=pr.part, cancel=cancel, runs=runs["loop"],
                                               recheck=unsure["loop"])
                loop_out.extend(_shift_seg(s, off) for s in tail_loop)
                dropped["loop"].extend(round(t0 + off, 1) for t0, _ in tr.dropped)
                tr.dropped = []
            timings["asr_loop"] = time.monotonic() - t
            del tr
            gc.collect()

        # 5) 합치기·쓰기
        pr.step(7)
        utts = merge.merge_tracks(merge.mic_utterances(mic_out), merge.assign_loop_segments(loop_out, turns))
        meta = dict(job.meta)
        meta.setdefault("started_at", None)
        if meta.get("seconds") is None:
            meta["seconds"] = round(audio_seconds, 1)
        meta["language"] = job.language or "auto"
        meta["processed_at"] = output.now_iso()
        meta["mode"] = "two_tracks" if (job.mic and job.loop) else "single_file"
        if lang_stats:
            meta["language_detection"] = lang_stats  # 트랙별 언어 판별 기술 정보(구간 수·언어별 초)
        meta["model"] = job.model
        meta["low_conf_marks"] = bool(job.mark_low_conf)
        meta["vad_gain"] = gains
        meta["mic_min_db"] = job.mic_min_db
        meta["recheck_unsure"] = bool(job.recheck_unsure)
        if pre_tracks:
            meta["bg_pre_seconds"] = {k: round(v.done_up_to, 1) for k, v in pre_tracks.items() if v.done_up_to > 0}
        if job.extra_terms:
            meta["meeting_terms"] = list(job.extra_terms)
        n_dropped = sum(len(v) for v in dropped.values())
        meta["tail_text_guard"] = {"on": bool(job.guard), "dropped": n_dropped,
                                   "times": {k: v for k, v in dropped.items() if v}}
        if n_dropped:  # 시각만 기록(글은 회의 내용이라 남기지 않음)
            log.info("말소리 없는 구간의 글 %d건 제외: %s", n_dropped,
                     ", ".join(f"{k} {t:.1f}초" for k, v in dropped.items() for t in v))
        if hint:
            meta["hints"] = {k: hint[k] for k in ("used", "dropped", "file", "read") if k in hint}
        names = {}
        if (job.folder / output.RESULT_JSON).is_file():
            try:  # 다시 처리할 때 전에 정한 이름은 번호가 바뀔 수 있어 쓰지 않음('나' 이름만 유지)
                _, _, old = output.load_result_json(job.folder)
                if old.get(merge.ME):
                    names[merge.ME] = old[merge.ME]
            except (ValueError, KeyError, OSError):
                pass
        outs = output.write_outputs(job.folder, utts, meta, names)
        pr.part(1.0)
        timings["total"] = time.monotonic() - t_all
        # 기술 정보만 기록(회의 내용 없음)
        log.info("처리 완료: 길이 %.0f초, 발언 %d개, 참석자 %d명, 소요 %.0f초",
                 audio_seconds, len(utts), len(merge.speakers_in(utts)), timings["total"])
        return JobResult(outs, len(utts), merge.speakers_in(utts), timings, audio_seconds, n_dropped)
    finally:
        for p in work.glob("*"):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            work.rmdir()
        except OSError:
            pass
