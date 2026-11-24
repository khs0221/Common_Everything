"""소리 장치 계층.

녹음 로직(`recorder.py`)은 이 파일의 `Backend`/`Source` 모양만 압니다.
- `SoundcardBackend`: 윈도우 실제 장치(soundcard 라이브러리, WASAPI). 마이크와
  스피커 루프백(스피커로 나가는 소리)을 읽습니다.
- `FakeBackend`: 장치 없는 시험용. 정해진 신호를 실제 시간 흐름에 맞춰 내보내고,
  루프백처럼 '소리가 없으면 데이터가 안 오는' 상황도 흉내 냅니다.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np

BLOCK_SECONDS = 0.1
WASAPI_BUFFER_SECONDS = 0.5


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    name: str
    is_default: bool = False


class Source(Protocol):
    samplerate: int

    def read(self) -> np.ndarray:
        """모노 float32 조각 1개를 돌려줍니다(데이터가 올 때까지 기다림)."""

    def close(self) -> None: ...


class Backend(Protocol):
    def list_microphones(self) -> list[DeviceInfo]: ...

    def list_speakers(self) -> list[DeviceInfo]: ...

    def open_microphone(self, device_id: str | None) -> Source: ...

    def open_loopback(self, speaker_id: str | None) -> Source: ...


def _init_com_for_thread() -> None:
    """윈도우: 녹음 스레드마다 COM 을 초기화합니다(이미 됐으면 무시)."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0x0)  # COINIT_MULTITHREADED
    except Exception:
        pass


def _run_in_mta(fn, *args, timeout: float = 30.0):
    """윈도우 COM(소리 장치)을 다루는 일을 별도 스레드에서 합니다.

    soundcard 는 불러오는 스레드를 COM 다중 스레드(MTA)로 초기화합니다. 화면(tkinter)의 주
    스레드가 MTA 가 되면 파일 선택 창(단일 스레드 COM 필요)이 오작동할 수 있어, 주 스레드에서는
    soundcard 를 불러오거나 부르지 않습니다.
    """
    box: dict = {}

    def work():
        _init_com_for_thread()
        try:
            box["r"] = fn(*args)
        except BaseException as e:  # 호출한 쪽에서 다시 던짐
            box["e"] = e

    t = threading.Thread(target=work, name="audio-com", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError("소리 장치가 응답하지 않습니다")
    if "e" in box:
        raise box["e"]
    return box.get("r")


class _SoundcardSource:
    """soundcard 의 recorder 를 감싼 조각 읽기."""

    # 16kHz 를 먼저 요청하고(WASAPI 자동 변환), 안 되면 장치 기본값 쪽으로 물러납니다.
    RATES = (16000, 48000, 44100)

    def __init__(self, mic_obj):
        _init_com_for_thread()
        last_err = None
        for sr in self.RATES:
            try:
                # blocksize 는 WASAPI 버퍼 길이(0.5초). 읽기는 0.1초씩 합니다.
                rec = mic_obj.recorder(samplerate=sr, channels=1, blocksize=int(sr * WASAPI_BUFFER_SECONDS))
                rec.__enter__()
            except Exception as e:  # 장치가 이 주파수를 거부
                last_err = e
                continue
            self._rec = rec
            self.samplerate = sr
            self._block = int(sr * BLOCK_SECONDS)
            return
        raise RuntimeError(f"장치를 열 수 없습니다: {last_err}")

    def read(self) -> np.ndarray:
        data = self._rec.record(numframes=self._block)
        data = np.asarray(data, dtype=np.float32)
        if data.ndim == 2:
            data = data.mean(axis=1)
        return data

    def close(self) -> None:
        try:
            self._rec.__exit__(None, None, None)
        except Exception:
            pass


class SoundcardBackend:
    """윈도우 실제 장치. (리눅스 컨테이너에서는 검증하지 못했습니다.)"""

    def __init__(self):
        # 늦게, 그리고 별도 스레드에서 불러옵니다(위 _run_in_mta 설명 참고).
        self._sc = _run_in_mta(lambda: __import__("soundcard"))

    def _list_mics(self) -> list[DeviceInfo]:
        try:
            default = self._sc.default_microphone().id
        except Exception:
            default = None
        return [DeviceInfo(m.id, m.name, m.id == default) for m in self._sc.all_microphones(include_loopback=False)]

    def _list_spks(self) -> list[DeviceInfo]:
        try:
            default = self._sc.default_speaker().id
        except Exception:
            default = None
        return [DeviceInfo(s.id, s.name, s.id == default) for s in self._sc.all_speakers()]

    def list_microphones(self) -> list[DeviceInfo]:
        return _run_in_mta(self._list_mics)

    def list_speakers(self) -> list[DeviceInfo]:
        return _run_in_mta(self._list_spks)

    def _open_mic(self, device_id: str | None) -> Source:
        mic = self._sc.default_microphone() if not device_id else self._sc.get_microphone(device_id)
        return _SoundcardSource(mic)

    def _open_loop(self, speaker_id: str | None) -> Source:
        if not speaker_id:
            speaker_id = self._sc.default_speaker().id
        lb = self._sc.get_microphone(speaker_id, include_loopback=True)
        return _SoundcardSource(lb)

    def open_microphone(self, device_id: str | None) -> Source:
        return _run_in_mta(self._open_mic, device_id)

    def open_loopback(self, speaker_id: str | None) -> Source:
        return _run_in_mta(self._open_loop, speaker_id)


# ---------------------------------------------------------------- 시험용 장치


class FakeSource:
    """실제 시간에 맞춰 신호를 내보내는 가짜 장치.

    signal(t0_frame, n) -> n 개 표본. gaps: [(시작초, 끝초)] 구간에는 데이터를 보내지 않고
    기다립니다(루프백이 무음일 때 WASAPI 가 아무것도 안 보내는 상황 흉내).
    fail_after_s: 이 시간이 지나면 read() 가 예외를 냅니다(장치 뽑힘 흉내).
    """

    def __init__(self, samplerate: int, signal: Callable[[int, int], np.ndarray],
                 gaps: list[tuple[float, float]] | None = None, fail_after_s: float | None = None,
                 clock: Callable[[], float] = time.monotonic, rate_error: float = 0.0):
        self.samplerate = samplerate
        self._signal = signal
        self._gaps = gaps or []
        self._fail_after = fail_after_s
        self._clock = clock
        self._start = clock()
        self._pos = 0  # 지금까지 '장치가 만든' 표본(무음 구간 포함)
        self._block = int(samplerate * BLOCK_SECONDS)
        self._rate = samplerate * (1.0 + rate_error)  # 장치 시계 오차 흉내
        self._closed = threading.Event()

    def _in_gap(self, t: float) -> bool:
        return any(a <= t < b for a, b in self._gaps)

    def read(self) -> np.ndarray:
        while not self._closed.is_set():
            elapsed = self._clock() - self._start
            if self._fail_after is not None and elapsed >= self._fail_after:
                raise OSError("가짜 장치 오류")
            avail = int(elapsed * self._rate) - self._pos
            if avail >= self._block:
                t = self._pos / self.samplerate
                start = self._pos
                self._pos += self._block
                if self._in_gap(t):
                    continue  # 무음 구간: 데이터를 버리고 아무것도 안 보냄
                return np.asarray(self._signal(start, self._block), dtype=np.float32)
            time.sleep(0.005)
        return np.zeros(0, dtype=np.float32)

    def close(self) -> None:
        self._closed.set()


def tone(freq: float, amp: float, samplerate: int) -> Callable[[int, int], np.ndarray]:
    def f(start: int, n: int) -> np.ndarray:
        t = (start + np.arange(n)) / samplerate
        return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)

    return f


class FakeBackend:
    def __init__(self, mic: Callable[[], FakeSource] | None = None, loop: Callable[[], FakeSource] | None = None):
        self._mic = mic or (lambda: FakeSource(16000, tone(440, 0.1, 16000)))
        self._loop = loop or (lambda: FakeSource(16000, tone(660, 0.1, 16000), gaps=[(1.0, 2.0)]))

    def list_microphones(self) -> list[DeviceInfo]:
        return [DeviceInfo("fake-mic", "시험용 마이크", True)]

    def list_speakers(self) -> list[DeviceInfo]:
        return [DeviceInfo("fake-spk", "시험용 스피커", True)]

    def open_microphone(self, device_id: str | None) -> Source:
        return self._mic()

    def open_loopback(self, speaker_id: str | None) -> Source:
        return self._loop()


def default_backend() -> Backend:
    if os.environ.get("MREC_FAKE_DEVICES") == "1":
        return FakeBackend()
    return SoundcardBackend()
