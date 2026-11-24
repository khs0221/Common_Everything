"""소리 장치 계층.

녹음 로직(`recorder.py`)은 이 파일의 `Backend`/`Source` 모양만 압니다.
- `SoundcardBackend`: 윈도우 실제 장치(soundcard 라이브러리, WASAPI). 마이크와
  스피커 루프백(스피커로 나가는 소리)을 읽습니다.
- `FakeBackend`: 장치 없는 시험용. 정해진 신호를 실제 시간 흐름에 맞춰 내보냅니다.
  무음 구간은 실제 soundcard 처럼 0 을 돌려주고(기본), 장치가 멈춘 상황(데이터가 안 옴)·
  장치 오류·기본 장치 변경·열기 지연도 흉내 낼 수 있습니다.

스레드 규칙(F1): 장치를 여는 것, 읽는 것, 닫는 것은 모두 같은 녹음 스레드에서 하고,
그 스레드는 시작할 때 COM 을 초기화합니다(`com_init`). soundcard 는 모듈을 불러오는 스레드에서만
CoInitializeEx 를 부르고(mediafoundation.py 116줄 `_com = _COMLibrary()`), 장치 열기
(`_audio_client` 의 IMMDevice::Activate, 411~419줄)·읽기(`GetNextPacketSize`, `GetBuffer`,
685~711줄)에서는 스레드별 초기화를 하지 않기 때문입니다.
"""
from __future__ import annotations

import importlib
import os
import sys
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
    started_at: float   # 스트림을 시작한 시각(time.monotonic 기준)
    device_id: str | None

    def read(self) -> np.ndarray:
        """모노 float32 조각 1개를 돌려줍니다(데이터가 올 때까지 기다림)."""

    def close(self) -> None: ...


class Backend(Protocol):
    def list_microphones(self) -> list[DeviceInfo]: ...

    def list_speakers(self) -> list[DeviceInfo]: ...

    def open_microphone(self, device_id: str | None) -> Source: ...

    def open_loopback(self, speaker_id: str | None) -> Source: ...

    def default_microphone_id(self) -> str | None: ...

    def default_speaker_id(self) -> str | None: ...


def com_init() -> bool:
    """윈도우: 지금 스레드의 COM 을 MTA 로 초기화합니다. 끝날 때 com_uninit 을 불러야 하면 True."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        hr = ctypes.windll.ole32.CoInitializeEx(None, 0x0)  # COINIT_MULTITHREADED
        return hr in (0, 1)  # S_OK, S_FALSE
    except Exception:
        return False


def com_uninit(initialized: bool) -> None:
    if initialized and os.name == "nt":
        try:
            import ctypes

            ctypes.windll.ole32.CoUninitialize()
        except Exception:
            pass


def _run_in_mta(fn, *args, timeout: float = 30.0):
    """윈도우 COM(소리 장치)을 다루는 일을 COM 을 초기화한 별도 스레드에서 합니다(목록 읽기 등).

    화면(tkinter)의 주 스레드가 MTA 가 되면 파일 선택 창(단일 스레드 COM 필요)이 오작동할 수 있어,
    주 스레드에서는 soundcard 를 부르지 않습니다. soundcard 를 '불러오는' 일은 여기서 하지 않습니다
    (먼저 COM 을 초기화하면 soundcard 의 불러오기가 실패함, import_audio_module 참고).
    """
    box: dict = {}

    def work():
        inited = com_init()
        try:
            box["r"] = fn(*args)
        except BaseException as e:  # 호출한 쪽에서 다시 던짐
            box["e"] = e
        finally:
            com_uninit(inited)

    t = threading.Thread(target=work, name="audio-com", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError("소리 장치가 응답하지 않습니다")
    if "e" in box:
        raise box["e"]
    return box.get("r")


class _SoundcardSource:
    """soundcard 의 recorder 를 감싼 조각 읽기. 만든 스레드에서만 읽고 닫아야 합니다."""

    # 16kHz 를 먼저 요청하고(WASAPI 자동 변환), 안 되면 장치 기본값 쪽으로 물러납니다.
    RATES = (16000, 48000, 44100)

    def __init__(self, mic_obj):
        self.device_id = getattr(mic_obj, "id", None)
        last_err = None
        for sr in self.RATES:
            try:
                # blocksize 는 WASAPI 버퍼 길이(0.5초). 읽기는 0.1초씩 합니다.
                rec = mic_obj.recorder(samplerate=sr, channels=1, blocksize=int(sr * WASAPI_BUFFER_SECONDS))
                rec.__enter__()
            except Exception as e:  # 장치가 이 주파수를 거부
                last_err = e
                continue
            self.started_at = time.monotonic()  # Start() 직후
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


_IMPORT_LOCK = threading.Lock()


def _release_import_thread_com(module) -> None:
    """soundcard 가 불러올 때 이 스레드에서 연 COM 을 같은 스레드에서 닫습니다.

    soundcard 는 모듈을 불러올 때만 CoInitializeEx 를 부르고, 닫는 것은 모듈이 사라질 때
    (`_COMLibrary.__del__`, 보통 프로그램 종료 때 주 스레드)입니다. 짝이 다른 스레드에서 맞지 않게
    여기서 같은 스레드에서 닫고 표시를 내립니다. 장치 객체는 id 문자열만 갖고 COM 객체는 부를 때마다
    새로 만들므로(mediafoundation.py 209~236줄 `_DeviceEnumerator`, 343~350줄 `_Device`),
    불러온 스레드의 COM 이 내려가도 COM 을 초기화한 다른 스레드에서 목록 읽기·열기가 됩니다.
    """
    if os.name != "nt":
        return
    try:
        com = sys.modules[module.__name__ + ".mediafoundation"]._com
        if getattr(com, "com_loaded", False):
            import ctypes

            ctypes.windll.ole32.CoUninitialize()
            com.com_loaded = False
    except Exception:
        pass


def import_audio_module(name: str = "soundcard", timeout: float = 30.0):
    """soundcard 를 'COM 을 아직 초기화하지 않은' 새 스레드에서 불러옵니다(R2 원인 수정).

    soundcard 는 불러올 때 CoInitializeEx 를 부르고, 결과가 S_OK(0)가 아니면 예외를 냅니다
    (mediafoundation.py 39~67줄 `_COMLibrary`, 78~105줄 `check_error`). 이미 COM 을 초기화한 스레드에서
    불러오면 S_FALSE(1)를 받아 `RuntimeError('Error 0x100000001')` 로 불러오기 자체가 실패합니다.
    그래서 이 스레드에서는 com_init() 을 부르지 않습니다. 주 스레드(화면)에서도 불러오지 않습니다
    (주 스레드가 MTA 가 되면 파일 선택 창이 오작동할 수 있음).

    불러오기가 실패하면 파이썬이 반쯤 불러온 모듈을 sys.modules 에서 지우므로, 다시 부르면
    처음부터 다시 불러옵니다.
    """
    with _IMPORT_LOCK:
        mod = sys.modules.get(name)
        if mod is not None:
            return mod
        box: dict = {}

        def work():
            try:
                m = importlib.import_module(name)
                _release_import_thread_com(m)
                box["m"] = m
            except BaseException as e:  # 호출한 쪽에서 다시 던짐
                box["e"] = e

        t = threading.Thread(target=work, name="audio-import", daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise TimeoutError("소리 장치 모듈을 불러오는 데 시간이 너무 오래 걸립니다")
        if "e" in box:
            raise box["e"]
        return box["m"]


class SoundcardBackend:
    """윈도우 실제 장치. (리눅스 컨테이너에서는 검증하지 못했습니다.)"""

    def __init__(self):
        # 늦게, 그리고 COM 을 초기화하지 않은 별도 스레드에서 불러옵니다(import_audio_module 설명).
        self._sc = import_audio_module("soundcard")

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

    # 아래 넷은 녹음 스레드(COM 초기화됨)에서 직접 부릅니다.
    def open_microphone(self, device_id: str | None) -> Source:
        return self._open_mic(device_id)

    def open_loopback(self, speaker_id: str | None) -> Source:
        return self._open_loop(speaker_id)

    def default_microphone_id(self) -> str | None:
        return self._sc.default_microphone().id

    def default_speaker_id(self) -> str | None:
        return self._sc.default_speaker().id


# ---------------------------------------------------------------- 시험용 장치


class FakeSource:
    """실제 시간에 맞춰 신호를 내보내는 가짜 장치.

    signal(시작 표본, n) -> n 개 표본.
    gaps: [(시작초, 끝초)] 무음 구간. gap_mode="zeros"(기본)는 실제 soundcard 처럼 0 을 돌려주고,
          "stall" 은 데이터를 안 보내고 그 구간 소리를 버립니다(장치가 멈춰 WASAPI 버퍼가 넘친 상황).
    fail_after_s: 이 시간이 지나면 read() 가 예외를 냅니다(장치 뽑힘 흉내).
    open_delay_s: 장치 열기에 걸리는 시간(만들 때 그만큼 기다림).
    """

    def __init__(self, samplerate: int, signal: Callable[[int, int], np.ndarray],
                 gaps: list[tuple[float, float]] | None = None, fail_after_s: float | None = None,
                 clock: Callable[[], float] = time.monotonic, rate_error: float = 0.0,
                 gap_mode: str = "zeros", open_delay_s: float = 0.0, device_id: str | None = None):
        if open_delay_s:
            time.sleep(open_delay_s)
        self.samplerate = samplerate
        self.device_id = device_id
        self._signal = signal
        self._gaps = gaps or []
        self._gap_mode = gap_mode
        self._fail_after = fail_after_s
        self._clock = clock
        self._start = clock()
        self.started_at = self._start
        self._pos = 0  # 지금까지 '장치가 만든' 표본(무음 구간 포함)
        self._block = int(samplerate * BLOCK_SECONDS)
        self._rate = samplerate * (1.0 + rate_error)  # 장치 시계 오차 흉내
        self._closed = threading.Event()
        self._cancel = threading.Event()
        self.reader_threads: set[int] = set()

    def _in_gap(self, t: float) -> bool:
        return any(a <= t < b for a, b in self._gaps)

    def read(self) -> np.ndarray:
        self.reader_threads.add(threading.get_ident())
        while not (self._closed.is_set() or self._cancel.is_set()):
            elapsed = self._clock() - self._start
            if self._fail_after is not None and elapsed >= self._fail_after:
                raise RuntimeError("가짜 장치 오류(장치 없어짐)")
            avail = int(elapsed * self._rate) - self._pos
            if avail >= self._block:
                t = self._pos / self.samplerate
                start = self._pos
                self._pos += self._block
                if self._in_gap(t):
                    if self._gap_mode == "stall":
                        continue  # 데이터를 버리고 아무것도 안 보냄
                    return np.zeros(self._block, dtype=np.float32)
                return np.asarray(self._signal(start, self._block), dtype=np.float32)
            time.sleep(0.005)
        return np.zeros(0, dtype=np.float32)

    def cancel(self) -> None:
        """기다리는 read() 를 빈 조각으로 돌려보냅니다(시험용 장치에만 있음)."""
        self._cancel.set()

    def close(self) -> None:
        self.reader_threads.add(threading.get_ident())
        self._closed.set()


def tone(freq: float, amp: float, samplerate: int) -> Callable[[int, int], np.ndarray]:
    def f(start: int, n: int) -> np.ndarray:
        t = (start + np.arange(n)) / samplerate
        return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)

    return f


class FakeBackend:
    """가짜 장치 묶음. mic/loop 은 부를 때마다 새 FakeSource 를 만드는 함수(다시 연결 시험용).

    default_mic / default_spk 를 바꾸면 '윈도우 기본 장치가 바뀐' 상황이 됩니다.
    """

    def __init__(self, mic: Callable[[], FakeSource] | None = None, loop: Callable[[], FakeSource] | None = None):
        self._mic = mic or (lambda: FakeSource(16000, tone(440, 0.1, 16000)))
        self._loop = loop or (lambda: FakeSource(16000, tone(660, 0.1, 16000), gaps=[(1.0, 2.0)]))
        self.default_mic = "fake-mic"
        self.default_spk = "fake-spk"
        self.opened: list[tuple[str, str | None, int]] = []  # (종류, 장치 id, 스레드)

    def list_microphones(self) -> list[DeviceInfo]:
        return [DeviceInfo("fake-mic", "시험용 마이크", True)]

    def list_speakers(self) -> list[DeviceInfo]:
        return [DeviceInfo("fake-spk", "시험용 스피커", True)]

    def _open(self, kind: str, factory, device_id: str | None, default: str) -> Source:
        src = factory()
        if getattr(src, "device_id", None) is None:
            src.device_id = device_id or default
        self.opened.append((kind, src.device_id, threading.get_ident()))
        return src

    def open_microphone(self, device_id: str | None) -> Source:
        return self._open("mic", self._mic, device_id, self.default_mic)

    def open_loopback(self, speaker_id: str | None) -> Source:
        return self._open("loop", self._loop, speaker_id, self.default_spk)

    def default_microphone_id(self) -> str | None:
        return self.default_mic

    def default_speaker_id(self) -> str | None:
        return self.default_spk


def default_backend() -> Backend:
    if os.environ.get("MREC_FAKE_DEVICES") == "1":
        return FakeBackend()
    return SoundcardBackend()
