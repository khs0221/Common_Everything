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
from .audio_io import TARGET_SR, prepare_16k_mono, wav_info
from .recorder import INFO_FILE, LOOP_FILE, MIC_FILE

log = logging.getLogger("mrec")

# 단계별 진행률 비중(합 1.0)
STEPS = [("준비", 0.03), ("말소리 찾기", 0.05), ("발언자 구분", 0.17), ("모델 읽기", 0.03),
         ("나(마이크) 글로 옮기기", 0.30), ("상대방(스피커) 글로 옮기기", 0.40), ("결과 쓰기", 0.02)]

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


@dataclass
class JobResult:
    outputs: dict
    utterances: int
    speakers: list[str]
    timings: dict
    audio_seconds: float


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
        lengths = [wav_info(p)[2] / TARGET_SR for p in (mic16, loop16) if p]
        audio_seconds = max(lengths) if lengths else 0.0

        # 2) 말소리 구간
        pr.step(1)
        t = time.monotonic()
        mic_segs = vad.speech_segments(mic16, lambda f: pr.part(f * 0.5), cancel=cancel) if mic16 else []
        loop_segs = vad.speech_segments(loop16, lambda f: pr.part(0.5 + f * 0.5), cancel=cancel) if loop16 else []
        timings["vad"] = time.monotonic() - t

        # 3) 발언자 구분(스피커 트랙)
        pr.step(2)
        t = time.monotonic()
        turns = []
        if loop16 and loop_segs:
            turns = diarize.diarize_file(loop16, job.num_speakers, progress=pr.part, cancel=cancel)
        gc.collect()
        timings["diarize"] = time.monotonic() - t

        # 4) 글로 옮기기
        pr.step(3)
        t = time.monotonic()
        tr = Transcriber(beam_size=job.beam_size)
        timings["load_asr"] = time.monotonic() - t
        pr.step(4)
        t = time.monotonic()
        mic_out = tr.transcribe_file(mic16, mic_segs, job.language, words=True,
                                     progress=pr.part, cancel=cancel) if mic16 else []
        timings["asr_mic"] = time.monotonic() - t
        pr.step(5)
        t = time.monotonic()
        loop_out = tr.transcribe_file(loop16, loop_segs, job.language, words=True,
                                      progress=pr.part, cancel=cancel) if loop16 else []
        timings["asr_loop"] = time.monotonic() - t
        del tr
        gc.collect()

        # 5) 합치기·쓰기
        pr.step(6)
        utts = merge.merge_tracks(merge.mic_utterances(mic_out), merge.assign_loop_segments(loop_out, turns))
        meta = dict(job.meta)
        meta.setdefault("started_at", None)
        if meta.get("seconds") is None:
            meta["seconds"] = round(audio_seconds, 1)
        meta["language"] = job.language or "auto"
        meta["processed_at"] = output.now_iso()
        meta["mode"] = "two_tracks" if (job.mic and job.loop) else "single_file"
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
        return JobResult(outs, len(utts), merge.speakers_in(utts), timings, audio_seconds)
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
