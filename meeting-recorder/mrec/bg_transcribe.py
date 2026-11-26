"""녹음 중 백그라운드 점진 전사(선택 기능, 기본 끔).

녹음이 진행되는 동안 3분(BG_CHUNK_SECONDS) 단위로 말소리 찾기·언어 판별·글로 옮기기를
낮은 스레드 수(최대 2스레드)로 미리 수행해 두어, 녹음 중지 후 대기 시간을 크게 줄입니다.

안전 원칙
- 녹음 파일 끝에서 BG_SAFE_LAG_SECONDS 만큼 여유를 두고 읽어 쓰기 충돌을 피합니다.
- 말이 이어지는 도중 잘리지 않도록 가급적 무음 경계에서만 청크를 끊습니다.
- 모든 예외는 내부에서 격리해 녹음 스레드에 절대 영향을 주지 않습니다(실패 시 중지 후 일반 처리로 자동 폴백).
- 중지 시 진행 중인 청크는 즉시 취소하고, 이미 완료된 구간 결과만 pipeline.run 에 넘깁니다.
"""
from __future__ import annotations

import gc
import logging
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .audio_io import slice_to_16k_mono, wav_info
from .recorder import LOOP_FILE, MIC_FILE
from .transcribe import Segment, Word

log = logging.getLogger("mrec")

BG_CHUNK_SECONDS = 180.0     # 한 번에 미리 처리할 녹음 길이(초)
BG_POLL_SECONDS = 5.0        # 녹음 파일 길이 확인 주기(초)
BG_SAFE_LAG_SECONDS = 2.0    # 녹음 중인 파일 끝에서 이만큼 여유를 두고 읽음
BG_MAX_THREADS = 2           # 녹음 중 CPU 점유 제한(화상회의 끊김 방지)


@dataclass
class TrackPreResult:
    done_up_to: float = 0.0
    speech: list[tuple[float, float]] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    dropped: list[float] = field(default_factory=list)


@dataclass
class PreTranscribed:
    """녹음 중 미리 전사된 결과(pipeline.Job.pre_transcribed 로 전달)."""
    language: str | None
    model: str
    mic_min_db: float | None
    tracks: dict[str, TrackPreResult] = field(default_factory=dict)

    def has_any(self) -> bool:
        return any(t.done_up_to > 0.0 for t in self.tracks.values())


def _choose_cut(segs: list[tuple[float, float]], dur: float) -> tuple[list[tuple[float, float]], float]:
    """청크 끝(dur)에서 말이 끊기지 않도록 무음 경계로 자를 시각과 포함할 구간을 정합니다."""
    if not segs:
        return [], dur
    if segs[-1][1] >= dur - 0.35:
        if len(segs) >= 2:
            cut = segs[-2][1]
            return segs[:-1], cut
        if segs[0][0] >= 5.0:
            return [], segs[0][0]
    return segs, dur


def _shift_segment(seg: Segment, offset: float) -> Segment:
    ws = [Word(w.start + offset, w.end + offset, w.text, w.prob) for w in seg.words]
    return Segment(seg.start + offset, seg.end + offset, seg.text, seg.lang, ws)


class BackgroundTranscriber:
    """녹음 중 백그라운드에서 일정 길이마다 점진 전사를 수행합니다."""

    def __init__(self, folder: Path, language: str | None = None, model: str = "turbo",
                 lid_model: str | None = "small", extra_terms: list[str] | None = None,
                 mic_min_db: float | None = -40.0, guard: bool = True,
                 paused: Callable[[], bool] | None = None,
                 chunk_seconds: float = BG_CHUNK_SECONDS,
                 poll_seconds: float = BG_POLL_SECONDS,
                 safe_lag_seconds: float = BG_SAFE_LAG_SECONDS):
        self.folder = Path(folder)
        self.language = language
        self.model = model
        self.lid_model = lid_model
        self.extra_terms = list(extra_terms or [])
        self.mic_min_db = mic_min_db
        self.guard = guard
        self._is_paused = paused or (lambda: False)
        self.chunk_seconds = float(chunk_seconds)
        self.poll_seconds = float(poll_seconds)
        self.safe_lag_seconds = float(safe_lag_seconds)

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._tracks = {"mic": TrackPreResult(), "loop": TrackPreResult()}
        self._work = self.folder / "_bg_work"
        self.failed = False
        self.chunks_done = 0
        self._thread = threading.Thread(target=self._run, name="bg-transcribe", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> PreTranscribed | None:
        """백그라운드 작업을 즉시 중단하고 완료된 구간 결과를 돌려줍니다."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)
        self._cleanup()
        return self.collect()

    def collect(self) -> PreTranscribed | None:
        with self._lock:
            tracks = {
                k: TrackPreResult(
                    done_up_to=v.done_up_to,
                    speech=list(v.speech),
                    segments=list(v.segments),
                    dropped=list(v.dropped),
                )
                for k, v in self._tracks.items()
            }
        pre = PreTranscribed(self.language, self.model, self.mic_min_db, tracks)
        return pre if pre.has_any() else None

    def _cleanup(self) -> None:
        try:
            if self._work.is_dir():
                shutil.rmtree(self._work, ignore_errors=True)
        except OSError:
            pass

    def _file_seconds(self, path: Path) -> float:
        if not path.is_file():
            return 0.0
        try:
            sr, _, frames = wav_info(path)
            return frames / sr if sr > 0 else 0.0
        except Exception:
            return 0.0

    def _run(self) -> None:
        from . import glossary, sysinfo, vad
        from .transcribe import Transcriber

        tr = None
        lid = None
        threads = min(BG_MAX_THREADS, sysinfo.compute_threads())
        try:
            self._work.mkdir(exist_ok=True)
            hint = glossary.load_hints(extra_terms=self.extra_terms)
            files = {"mic": self.folder / MIC_FILE, "loop": self.folder / LOOP_FILE}

            while not self._stop.is_set():
                did_work = False
                if not self._is_paused():
                    for key, wav_path in files.items():
                        if self._stop.is_set():
                            break
                        with self._lock:
                            cursor = self._tracks[key].done_up_to
                        avail = self._file_seconds(wav_path) - self.safe_lag_seconds
                        if avail - cursor < self.chunk_seconds:
                            continue
                        raw_end = avail
                        chunk_wav = self._work / f"{key}_chunk.wav"
                        slice_to_16k_mono(wav_path, chunk_wav, cursor, raw_end)
                        dur = self._file_seconds(chunk_wav)
                        if dur < 0.5:
                            continue
                        min_db = self.mic_min_db if key == "mic" else None
                        raw_segs = vad.speech_segments(chunk_wav, cancel=self._stop.is_set, min_db=min_db)
                        segs, cut = _choose_cut(raw_segs, dur)
                        chunk_out: list[Segment] = []
                        chunk_dropped: list[float] = []
                        if segs:
                            runs = None
                            if self.language is None:
                                lid_name = self.lid_model or self.model
                                if lid is None:
                                    lid = Transcriber(threads=threads, model=lid_name)
                                runs = lid.detect_language_runs(chunk_wav, segs, cancel=self._stop.is_set)
                            if tr is None:
                                tr = Transcriber(threads=threads, model=self.model,
                                                 hotwords=hint.get("hotwords"),
                                                 hint_vocab_text=hint.get("terms_text"))
                                tr.guard = bool(self.guard)
                                tr.recheck = False
                            tr.dropped = []
                            raw_out = tr.transcribe_file(chunk_wav, segs, self.language, words=True,
                                                         cancel=self._stop.is_set, runs=runs)
                            chunk_out = [_shift_segment(s, cursor) for s in raw_out]
                            chunk_dropped = [round(t0 + cursor, 1) for t0, _ in tr.dropped]
                        try:
                            chunk_wav.unlink()
                        except OSError:
                            pass
                        with self._lock:
                            tp = self._tracks[key]
                            tp.speech.extend((a + cursor, b + cursor) for a, b in segs)
                            tp.segments.extend(chunk_out)
                            tp.dropped.extend(chunk_dropped)
                            tp.done_up_to = cursor + cut
                        self.chunks_done += 1
                        did_work = True
                        log.info("녹음 중 미리 처리(%s): %.0f~%.0f초 완료(발언 %d개)",
                                 key, cursor, cursor + cut, len(chunk_out))
                if not did_work:
                    self._stop.wait(self.poll_seconds)
        except InterruptedError:
            pass
        except Exception as e:
            self.failed = True
            log.warning("녹음 중 미리 처리 중단(녹음에는 영향 없음): %s: %s", type(e).__name__, e)
        finally:
            del tr, lid
            gc.collect()
            self._cleanup()
