"""두 트랙 녹음(마이크 = 나, 스피커 루프백 = 상대방).

스레드 구성(트랙마다 2개)
- 녹음 스레드: COM 초기화 → 장치 열기 → 읽기 → (끊기면 다시 열기) → 닫기. 장치를 여는 스레드와
  읽는 스레드가 같습니다(soundcard 의 COM 객체를 만든 스레드에서만 다룸, 감사 F1).
- 쓰기 스레드: 녹음 스레드가 줄에 넣은 조각을 디스크에 씁니다(디스크가 잠깐 느려도 장치 버퍼가 넘치지 않게).

시간축 맞추기
- 두 장치의 스트림 시작 시각 중 이른 쪽을 녹음 출발 시각(t0)으로 삼고, 늦게 열린 트랙은 그 차이만큼
  앞을 무음으로 채웁니다(감사 F3). 장치를 다시 연 경우에도 새 스트림 시작 시각 위치까지 무음을 채웁니다.
- 그 밖에는 장치가 준 표본을 이어 씁니다(soundcard 는 루프백이 조용할 때 경과 시간만큼 0 을 줍니다).
  조각이 도착한 시각보다 쓴 길이가 LAG_TOLERANCE_S 넘게 모자라면(장치가 멈춰 버퍼가 넘친 경우)
  모자란 만큼 무음을 채웁니다. 이 값은 WASAPI 버퍼(0.5초)보다 커서, 버퍼 안에서 늦게 읽은 정상
  데이터를 무음으로 밀어내지 않습니다.
- 장치 오류·기본 장치 변경 때는 1초마다 다시 열기를 시도하고(감사 F2), 그동안의 빈 시간은 무음으로 채웁니다.
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

from .audio_io import Resampler, WavTrackWriter
from .devices import WASAPI_BUFFER_SECONDS, Backend, Source, com_init, com_uninit

MIC_FILE = "마이크_나.wav"
LOOP_FILE = "스피커_상대방.wav"
INFO_FILE = "녹음정보.json"

LAG_TOLERANCE_S = WASAPI_BUFFER_SECONDS + 0.25  # 0.75초
RECONNECT_INTERVAL_S = 1.0
DEFAULT_PROBE_S = 2.0     # '기본 장치 따라가기' 일 때 기본 장치가 바뀌었는지 보는 간격
OPEN_TIMEOUT_S = 15.0
STOP_JOIN_S = 2.0

KIND_NAMES = {"mic": "마이크", "loop": "스피커"}


def level_dbfs(x: np.ndarray) -> float:
    if x.size == 0:
        return -90.0
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return max(-90.0, 20.0 * np.log10(rms + 1e-9))


@dataclass
class TrackStats:
    frames_written: int = 0
    frames_padded: int = 0      # 채운 무음 전체(시작 보정 포함)
    lead_in_frames: int = 0     # 시작 시각 차이 보정으로 앞에 채운 무음
    pad_events: int = 0
    max_lead_s: float = 0.0
    reconnects: int = 0
    default_changes: int = 0
    error: str | None = None    # 마지막 오류(기술 정보)
    samplerate: int = 0
    reader_alive_after_stop: bool = False


class AlignedTrack:
    """한 트랙: 장치 열기·읽기(녹음 스레드)와 시각 맞춘 쓰기(쓰기 스레드)."""

    def __init__(self, name: str, opener: Callable[[], Source], clock: Callable[[], float],
                 stop: threading.Event, default_probe: Callable[[], str | None] | None = None,
                 path: Path | None = None, t0: float | None = None, samplerate: int | None = None):
        self.name = name
        self.opener = opener
        self.clock = clock
        self.stop_event = stop
        self.default_probe = default_probe  # None 이면 고른 장치에 고정
        self.source: Source | None = None
        self.device_name: str | None = None
        self._source_closed = True
        self.sr: int | None = samplerate
        self.started_at: float | None = None  # 처음 스트림 시작 시각
        self.opened_id: str | None = None
        self.ready = threading.Event()
        self.open_error: BaseException | None = None
        self.state = "opening"  # opening | ok | reconnecting | stopped
        self.stats = TrackStats()
        self.level = -90.0
        self.t0 = t0
        self.writer: WavTrackWriter | None = None
        self._rs: Resampler | None = None
        self._lock = threading.Lock()
        self._q: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, name=f"rec-{name}", daemon=True)
        self.writer_thread = threading.Thread(target=self._write_loop, name=f"wr-{name}", daemon=True)
        if path is not None:  # 시험에서 장치 없이 write_block 만 쓸 때
            self.attach_writer(path, t0 or 0.0)

    # ------------------------------------------------------------ 쓰기 쪽
    def attach_writer(self, path: Path, t0: float) -> None:
        assert self.sr, "표본 주파수를 모릅니다"
        self.t0 = t0
        self.writer = WavTrackWriter(path, self.sr)
        self.stats.samplerate = self.sr

    def write_block(self, block: np.ndarray, now: float) -> None:
        """조각을 시각에 맞춰 씁니다(시험에서 직접 부를 수 있게 분리)."""
        with self._lock:
            w = self.writer
            if w is None or w.closed:
                return
            expected_end = int(round((now - self.t0) * self.sr))
            lag = expected_end - len(block) - w.frames_written
            if lag > int(LAG_TOLERANCE_S * self.sr):
                w.write_silence(lag)
                self.stats.frames_padded += lag
                self.stats.pad_events += 1
            w.write(block)
            lead = (w.frames_written - expected_end) / self.sr
            if lead > self.stats.max_lead_s:
                self.stats.max_lead_s = lead
            self.stats.frames_written = w.frames_written

    def sync_to(self, stream_start: float, lead_in: bool = False) -> None:
        """스트림이 stream_start 에 시작했다면 그 위치까지 무음을 채웁니다."""
        with self._lock:
            w = self.writer
            if w is None or w.closed:
                return
            target = int(round((stream_start - self.t0) * self.sr))
            missing = target - w.frames_written
            if missing > 0:
                w.write_silence(missing)
                self.stats.frames_padded += missing
                if lead_in:
                    self.stats.lead_in_frames += missing
                else:
                    self.stats.pad_events += 1
            self.stats.frames_written = w.frames_written

    def _write_loop(self) -> None:
        failed = False
        first_sync = True
        while True:
            item = self._q.get()
            if item is None:
                break
            if failed:
                continue  # 쓰기 실패 뒤에는 줄만 비웁니다(메모리 증가 방지)
            try:
                if item[0] == "sync":
                    self.sync_to(item[1], lead_in=first_sync)
                    first_sync = False
                else:
                    self.write_block(item[1], item[2])
            except OSError as e:  # 디스크 가득 참 등
                self.stats.error = f"디스크 쓰기 오류: {e}"
                failed = True

    # ------------------------------------------------------------ 장치 쪽(녹음 스레드)
    def _open(self) -> None:
        src = self.opener()
        started = getattr(src, "started_at", None) or self.clock()
        self.source = src
        self._source_closed = False
        if self.sr is None:
            self.sr = int(src.samplerate)
        self._rs = None if int(src.samplerate) == self.sr else Resampler(int(src.samplerate), self.sr)
        self.opened_id = getattr(src, "device_id", None)
        self.device_name = getattr(src, "device_name", None) or self.opened_id
        if self.started_at is None:
            self.started_at = started
        self._q.put(("sync", started))
        self.state = "ok"

    def _close_source(self) -> None:
        """지금 장치를 닫습니다(참조는 남겨 둠: 닫힌 마지막 장치를 확인할 수 있게)."""
        src = self.source
        if src is not None and not self._source_closed:
            self._source_closed = True
            try:
                src.close()
            except Exception:
                pass

    def _reconnect(self, reason: str) -> bool:
        """다시 열릴 때까지 RECONNECT_INTERVAL_S 마다 시도합니다. 중지되면 False."""
        self.state = "reconnecting"
        self.level = -90.0
        self.stats.error = reason
        self._close_source()
        while not self.stop_event.is_set():
            try:
                self._open()
                self.stats.reconnects += 1
                return True
            except Exception as e:
                self.stats.error = f"{reason} / 다시 열기 실패: {type(e).__name__}: {e}"
            if self.stop_event.wait(RECONNECT_INTERVAL_S):
                break
        return False

    def _run(self) -> None:
        inited = com_init()
        try:
            try:
                self._open()
            except BaseException as e:
                self.open_error = e
                return
            finally:
                self.ready.set()
            last_probe = self.clock()
            while not self.stop_event.is_set():
                try:
                    block = self.source.read()
                except Exception as e:  # 장치 뽑힘 등
                    if not self._reconnect(f"장치 오류: {type(e).__name__}: {e}"):
                        break
                    continue
                now = self.clock()
                if block.size:
                    if self._rs is not None:
                        block = self._rs.process(block)
                    self.level = level_dbfs(block)
                    self._q.put(("data", block, now))  # 중지 직후 도착한 조각도 씁니다
                if self.default_probe and now - last_probe >= DEFAULT_PROBE_S:
                    last_probe = now
                    try:
                        cur = self.default_probe()
                    except Exception:
                        cur = None
                    if cur and self.opened_id and cur != self.opened_id:
                        self.stats.default_changes += 1
                        if not self._reconnect("윈도우 기본 장치가 바뀌어 다시 연결"):
                            break
        finally:
            self.state = "stopped"
            self._q.put(None)
            self._close_source()
            com_uninit(inited)

    def cancel_read(self) -> None:
        """시험용 장치는 기다리는 read() 를 바로 돌려보낼 수 있습니다(실제 장치는 0.1초 안에 돌아옴)."""
        src = self.source
        if src is not None and hasattr(src, "cancel"):
            try:
                src.cancel()
            except Exception:
                pass

    def finish(self, timeout: float) -> None:
        """녹음 스레드가 끝나길 기다리고, 줄에 남은 조각을 모두 쓴 뒤 돌아옵니다."""
        self.thread.join(timeout)
        if self.thread.is_alive():  # 장치가 응답하지 않음: 쓰기 스레드만 끝냄
            self.stats.reader_alive_after_stop = True
            self._q.put(None)
        if self.writer_thread.is_alive():
            self.writer_thread.join(max(timeout, 10.0))

    def pad_to_seconds(self, seconds: float) -> None:
        with self._lock:
            w = self.writer
            if w is None or w.closed:
                return
            target = int(round(seconds * self.sr))
            missing = target - w.frames_written
            if missing > 0:
                w.write_silence(missing)
                self.stats.frames_padded += missing
            self.stats.frames_written = w.frames_written

    def close(self) -> None:
        with self._lock:
            if self.writer is not None:
                self.writer.close()

    def problem(self) -> str | None:
        """화면에 띄울 지금의 문제(없으면 None)."""
        if self.state == "reconnecting":
            return f"{KIND_NAMES.get(self.name, self.name)} 장치가 끊겼습니다. 다시 연결하는 중이며, 그동안은 무음으로 채웁니다."
        if self.stats.error and self.stats.error.startswith("디스크"):
            return f"{KIND_NAMES.get(self.name, self.name)} 녹음 파일을 쓰지 못하고 있습니다. 저장 공간을 확인해 주십시오."
        return None


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
    """녹음 시작/중지. 장치 계층(Backend)을 바꿔 끼울 수 있습니다.

    mic_id / speaker_id 가 None 이면 '윈도우 기본 장치' 를 쓰고, 녹음 중 기본 장치가 바뀌면 따라갑니다.
    """

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
        self._stop = threading.Event()
        b = self.backend
        probe_mic = getattr(b, "default_microphone_id", None) if mic_id is None else None
        probe_spk = getattr(b, "default_speaker_id", None) if speaker_id is None else None
        self.tracks = {
            "mic": AlignedTrack("mic", lambda: b.open_microphone(mic_id), self.clock, self._stop, probe_mic),
            "loop": AlignedTrack("loop", lambda: b.open_loopback(speaker_id), self.clock, self._stop, probe_spk),
        }
        # 1) 두 장치를 각자의 녹음 스레드에서 엽니다(읽기도 곧바로 시작해 버퍼가 넘치지 않게).
        for t in self.tracks.values():
            t.thread.start()
        deadline = time.monotonic() + OPEN_TIMEOUT_S
        for t in self.tracks.values():
            t.ready.wait(max(0.0, deadline - time.monotonic()))
        errs = [t.open_error or (None if t.ready.is_set() else TimeoutError("장치 열기 시간 초과"))
                for t in self.tracks.values()]
        errs = [e for e in errs if e is not None]
        if errs:
            # 실패하면 폴더를 만들지 않습니다.
            self._abort_start()
            raise errs[0]
        # 2) 출발 시각 = 두 스트림 중 먼저 시작한 시각. 늦은 쪽은 앞을 무음으로 채웁니다.
        self.t0 = min(t.started_at for t in self.tracks.values())
        self.started_at = datetime.now()
        self.folder = None
        try:
            self.folder = new_meeting_folder(output_root, self.started_at)
            self.tracks["mic"].attach_writer(self.folder / MIC_FILE, self.t0)
            self.tracks["loop"].attach_writer(self.folder / LOOP_FILE, self.t0)
            self._write_info(finished=False)
        except BaseException:
            # 폴더·파일 준비가 실패하면(디스크 가득 참 등) 이미 읽고 있는 녹음 스레드를 멈추고
            # 장치를 닫습니다. 멈추지 않으면 쓰이지 않는 조각이 줄에 계속 쌓입니다(감사 N1).
            self._abort_start()
            raise
        for t in self.tracks.values():
            t.writer_thread.start()
        self.running = True
        return self.folder

    def _abort_start(self) -> None:
        self._stop.set()
        for t in self.tracks.values():
            t.cancel_read()
        for t in self.tracks.values():
            t.thread.join(STOP_JOIN_S)
            if t.thread.is_alive():
                t.stats.reader_alive_after_stop = True
            try:
                t.close()  # 만들어진 파일이 있으면 닫음
            except OSError:
                pass
        self._remove_partial_folder()
        self.tracks = {}
        self.running = False

    def _remove_partial_folder(self) -> None:
        """시작 실패로 남은 빈 회의 폴더와, 소리 없이 머리말만 있는 우리 파일만 지웁니다(감사 E3).
        폴더에 다른 파일이 있거나 소리가 든 wav 가 있으면 아무것도 지우지 않습니다."""
        folder = self.folder
        if folder is None or not folder.is_dir():
            return
        ours = {MIC_FILE, LOOP_FILE, INFO_FILE}
        try:
            names = {p.name for p in folder.iterdir()}
            if names - ours:
                return
            for name in (MIC_FILE, LOOP_FILE):
                p = folder / name
                if p.is_file() and p.stat().st_size > 44:  # 44바이트 = 소리 없는 WAV 머리말
                    return
            for name in names:
                (folder / name).unlink()
            folder.rmdir()
            self.folder = None
        except OSError:
            pass

    def elapsed(self) -> float:
        return self.clock() - self.t0 if self.running else 0.0

    def levels(self) -> dict[str, float]:
        return {k: t.level for k, t in self.tracks.items()}

    def errors(self) -> dict[str, str]:
        """기록된 마지막 오류(다시 연결된 뒤에도 남음, 기술 정보)."""
        return {k: t.stats.error for k, t in self.tracks.items() if t.stats.error}

    def device_names(self) -> dict[str, str | None]:
        """실제로 열린 장치 이름(진단용)."""
        return {k: t.device_name for k, t in self.tracks.items()}

    def problems(self) -> dict[str, str]:
        """지금 화면에 경고로 띄울 문제."""
        out = {}
        for k, t in self.tracks.items():
            p = t.problem()
            if p:
                out[k] = p
        return out

    def stop(self, join_timeout: float = STOP_JOIN_S) -> RecordingResult:
        if not self.running:
            raise RuntimeError("녹음 중이 아닙니다")
        end = self.clock() - self.t0
        self._stop.set()
        # 장치는 각 녹음 스레드가 스스로 닫습니다(읽는 도중 다른 스레드에서 닫지 않음).
        for t in self.tracks.values():
            t.cancel_read()
        for t in self.tracks.values():
            t.finish(join_timeout)
        # 두 트랙을 같은 길이로 맞춥니다.
        longest = max([end] + [t.writer.seconds for t in self.tracks.values() if t.writer])
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
