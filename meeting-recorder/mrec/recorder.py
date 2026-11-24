"""두 트랙 녹음(마이크 = 나, 스피커 루프백 = 상대방).

시간축 맞추기
- 두 트랙은 같은 출발 시각(t0)을 기준으로 씁니다.
- 조각이 도착할 때마다 '지금까지 흐른 시간 × 표본 주파수' 와 실제로 쓴 표본 수를 비교해,
  모자라면(루프백이 무음이라 데이터가 안 온 경우, 장치가 잠시 멈춘 경우) 그만큼 무음을
  채운 뒤 씁니다. 그래서 두 파일의 같은 위치는 같은 시각입니다.
- 장치가 오류로 멈추면 그 트랙은 중지 때까지 무음으로 채워 길이를 맞춥니다.
- 중지할 때 두 트랙을 같은 길이(초)로 맞춥니다.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from .audio_io import WavTrackWriter
from .devices import Backend, Source

MIC_FILE = "마이크_나.wav"
LOOP_FILE = "스피커_상대방.wav"
INFO_FILE = "녹음정보.json"

# 이 이상 뒤처지면 무음을 채웁니다(초). 장치 버퍼 지연보다 커야 합니다.
LAG_TOLERANCE_S = 0.25


def level_dbfs(x: np.ndarray) -> float:
    if x.size == 0:
        return -90.0
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return max(-90.0, 20.0 * np.log10(rms + 1e-9))


@dataclass
class TrackStats:
    frames_written: int = 0
    frames_padded: int = 0
    pad_events: int = 0
    max_lead_s: float = 0.0
    error: str | None = None
    samplerate: int = 0


class AlignedTrack:
    """한 트랙: 장치에서 읽어 시각에 맞춰 디스크에 씁니다."""

    def __init__(self, name: str, source: Source, path: Path, t0: float,
                 clock: Callable[[], float], stop: threading.Event):
        self.name = name
        self.source = source
        self.sr = int(source.samplerate)
        self.writer = WavTrackWriter(path, self.sr)
        self.t0 = t0
        self.clock = clock
        self.stop_event = stop
        self.stats = TrackStats(samplerate=self.sr)
        self.level = -90.0
        self._lock = threading.Lock()
        # 장치 읽기와 디스크 쓰기를 다른 스레드로 나눕니다. 디스크가 잠깐 느려져도(fsync 등)
        # 장치 버퍼를 제때 비워 소리가 빠지지 않게 하기 위함입니다.
        self._q: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, name=f"rec-{name}", daemon=True)
        self.writer_thread = threading.Thread(target=self._write_loop, name=f"wr-{name}", daemon=True)

    def start(self) -> None:
        self.writer_thread.start()
        self.thread.start()

    def write_block(self, block: np.ndarray, now: float) -> None:
        """조각을 시각에 맞춰 씁니다(시험에서 직접 부를 수 있게 분리)."""
        with self._lock:
            if self.writer.closed:
                return
            expected_end = int(round((now - self.t0) * self.sr))
            expected_start = expected_end - len(block)
            lag = expected_start - self.writer.frames_written
            if lag > int(LAG_TOLERANCE_S * self.sr):
                self.writer.write_silence(lag)
                self.stats.frames_padded += lag
                self.stats.pad_events += 1
            self.writer.write(block)
            lead = (self.writer.frames_written - expected_end) / self.sr
            if lead > self.stats.max_lead_s:
                self.stats.max_lead_s = lead
            self.stats.frames_written = self.writer.frames_written

    def _run(self) -> None:
        """장치 읽기 스레드: 조각과 도착 시각을 줄에 넣기만 합니다."""
        try:
            while True:
                block = self.source.read()
                now = self.clock()
                if block.size:
                    self.level = level_dbfs(block)
                    self._q.put((block, now))  # 중지 직후 도착한 조각도 씁니다(마지막 말 끝 보존)
                if self.stop_event.is_set():
                    break
        except Exception as e:  # 장치 뽑힘 등: 기록만 하고 나머지 트랙은 계속
            self.stats.error = f"{type(e).__name__}: {e}"
            self.level = -90.0
        finally:
            self._q.put(None)
            try:
                self.source.close()
            except Exception:
                pass

    def _write_loop(self) -> None:
        """디스크 쓰기 스레드."""
        failed = False
        while True:
            item = self._q.get()
            if item is None:
                break
            if failed:
                continue  # 쓰기 실패 뒤에는 줄만 비웁니다(메모리 증가 방지)
            try:
                self.write_block(*item)
            except OSError as e:  # 디스크 가득 참 등
                self.stats.error = f"디스크 쓰기 오류: {e}"
                failed = True

    def finish(self, timeout: float) -> None:
        """읽기 스레드가 끝나길 기다리고, 줄에 남은 조각을 모두 쓴 뒤 돌아옵니다."""
        self.thread.join(timeout)
        if self.thread.is_alive():  # 장치가 응답하지 않음: 쓰기 스레드만 끝냄
            self._q.put(None)
        self.writer_thread.join(max(timeout, 10.0))

    def pad_to_seconds(self, seconds: float) -> None:
        with self._lock:
            if self.writer.closed:
                return
            target = int(round(seconds * self.sr))
            missing = target - self.writer.frames_written
            if missing > 0:
                self.writer.write_silence(missing)
                self.stats.frames_padded += missing
            self.stats.frames_written = self.writer.frames_written

    def close(self) -> None:
        with self._lock:
            self.writer.close()


@dataclass
class RecordingResult:
    folder: Path
    mic_path: Path
    loop_path: Path
    seconds: float
    stats: dict = field(default_factory=dict)


def new_meeting_folder(root: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    base = root / now.strftime("회의_%Y%m%d_%H%M")
    p = base
    i = 2
    while p.exists():
        p = Path(f"{base}_{i}")
        i += 1
    p.mkdir(parents=True)
    return p


class Recorder:
    """녹음 시작/중지. 장치 계층(Backend)을 바꿔 끼울 수 있습니다."""

    def __init__(self, backend: Backend, clock: Callable[[], float] = time.monotonic):
        self.backend = backend
        self.clock = clock
        self.tracks: dict[str, AlignedTrack] = {}
        self.folder: Path | None = None
        self.t0 = 0.0
        self._stop = threading.Event()
        self.running = False
        self.started_at: datetime | None = None

    def start(self, output_root: Path, mic_id: str | None, speaker_id: str | None) -> Path:
        if self.running:
            raise RuntimeError("이미 녹음 중입니다")
        output_root.mkdir(parents=True, exist_ok=True)
        # 장치를 먼저 열어 보고, 실패하면 폴더를 만들지 않습니다.
        mic = self.backend.open_microphone(mic_id)
        try:
            loop = self.backend.open_loopback(speaker_id)
        except Exception:
            mic.close()
            raise
        self.started_at = datetime.now()
        self.folder = new_meeting_folder(output_root, self.started_at)
        self._stop = threading.Event()
        self.t0 = self.clock()
        self.tracks = {
            "mic": AlignedTrack("mic", mic, self.folder / MIC_FILE, self.t0, self.clock, self._stop),
            "loop": AlignedTrack("loop", loop, self.folder / LOOP_FILE, self.t0, self.clock, self._stop),
        }
        self._write_info(finished=False)
        for t in self.tracks.values():
            t.start()
        self.running = True
        return self.folder

    def elapsed(self) -> float:
        return self.clock() - self.t0 if self.running else 0.0

    def levels(self) -> dict[str, float]:
        return {k: t.level for k, t in self.tracks.items()}

    def errors(self) -> dict[str, str]:
        return {k: t.stats.error for k, t in self.tracks.items() if t.stats.error}

    def stop(self, join_timeout: float = 3.0) -> RecordingResult:
        if not self.running:
            raise RuntimeError("녹음 중이 아닙니다")
        end = self.clock() - self.t0
        self._stop.set()
        # 장치는 각 녹음 스레드가 스스로 닫습니다(읽는 도중 다른 스레드에서 닫지 않음).
        for t in self.tracks.values():
            t.finish(join_timeout)
        # 두 트랙을 같은 길이로 맞춥니다.
        longest = max([end] + [t.writer.seconds for t in self.tracks.values()])
        for t in self.tracks.values():
            try:
                t.pad_to_seconds(longest)
            except OSError as e:
                t.stats.error = t.stats.error or f"디스크 쓰기 오류: {e}"
            try:
                t.close()
            except OSError as e:
                t.stats.error = t.stats.error or f"파일 닫기 오류: {e}"
        self.running = False
        self._write_info(finished=True, seconds=longest)
        assert self.folder is not None
        return RecordingResult(self.folder, self.folder / MIC_FILE, self.folder / LOOP_FILE, longest,
                               {k: vars(t.stats) for k, t in self.tracks.items()})

    def _write_info(self, finished: bool, seconds: float | None = None) -> None:
        """녹음정보.json: 기술 정보만(회의 내용 없음)."""
        assert self.folder is not None
        info = {
            "started_at": self.started_at.isoformat(timespec="seconds") if self.started_at else None,
            "finished": finished,
            "seconds": round(seconds, 3) if seconds is not None else None,
            "files": {"mic": MIC_FILE, "loop": LOOP_FILE},
            "tracks": {k: vars(t.stats) for k, t in self.tracks.items()},
        }
        (self.folder / INFO_FILE).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
