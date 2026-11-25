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
import re
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


@dataclass(frozen=True)
class OpenCombo:
    """장치를 여는 한 가지 방법."""
    method: str            # 'soundcard'(라이브러리 그대로) / 'direct'(형식을 직접 만들어 Initialize)
    samplerate: int | None  # None = 장치 믹스 형식의 주파수
    channels: int | None    # None = 장치 믹스 형식의 채널 수(읽은 뒤 모노로 합침)
    buffer_s: float | None  # WASAPI 버퍼 길이(초). None = soundcard 기본값

    def label(self) -> str:
        sr = self.samplerate or "장치값"
        ch = self.channels or "장치값"
        buf = f"{self.buffer_s}s" if self.buffer_s else "기본"
        return f"{self.method}/{sr}Hz/{ch}ch/버퍼{buf}"


# 열기 순서(R5). 앞의 세 개는 R4 까지와 같아, 지금 열리는 장치는 똑같이 열립니다.
# 'direct' 는 soundcard 가 장치 믹스 형식을 assert 로 검사하다 문자열 없는 AssertionError 로 실패하는
# 경우(mediafoundation.py 516~525줄)를 피하려고, 표준 배치(40바이트)의 32비트 float 형식을 직접 만들어
# 같은 플래그(AUTOCONVERTPCM 등)로 Initialize 합니다.
OPEN_COMBOS = (
    OpenCombo("soundcard", 16000, 1, WASAPI_BUFFER_SECONDS),
    OpenCombo("soundcard", 48000, 1, WASAPI_BUFFER_SECONDS),
    OpenCombo("soundcard", 44100, 1, WASAPI_BUFFER_SECONDS),
    OpenCombo("direct", 16000, 1, WASAPI_BUFFER_SECONDS),
    OpenCombo("direct", 48000, 1, WASAPI_BUFFER_SECONDS),
    OpenCombo("direct", None, None, WASAPI_BUFFER_SECONDS),
    OpenCombo("soundcard", 48000, None, None),
)


class DeviceOpenError(RuntimeError):
    """모든 열기 조합이 실패. attempts(조합별 실패)와 device_info(장치 속성)를 담습니다."""

    def __init__(self, name: str | None, attempts: list[dict], device_info: dict):
        self.device = name
        self.attempts = attempts
        self.device_info = device_info
        super().__init__(self.describe())

    def describe(self) -> str:
        lines = [f"장치를 열 수 없습니다(시도한 방법 {len(self.attempts)}가지 모두 실패): {self.device or '?'}"]
        for a in self.attempts:
            hr = f", HRESULT {a['hresult']}" if a.get("hresult") else ""
            where = f" @ {a['where']}" if a.get("where") else ""
            lines.append(f"- {a['combo']}: {a['type']} {a['repr']}{hr}{where}")
        if self.device_info:
            lines.append("장치 속성: " + ", ".join(f"{k}={v}" for k, v in self.device_info.items()))
        return "\n".join(lines)


def describe_exception(e: BaseException) -> dict:
    """예외 종류·repr·HRESULT·마지막 위치(파일:줄 함수) — 메시지가 빈 예외도 구별되게."""
    import traceback

    hr = None
    m = re.search(r"0x[0-9a-fA-F]{8}", str(e))
    if m:
        hr = m.group(0).lower()
    where = ""
    try:
        tb = traceback.extract_tb(e.__traceback__)
        if tb:
            f = tb[-1]
            where = f"{os.path.basename(f.filename)}:{f.lineno} {f.name}"
    except Exception:
        pass
    return {"type": type(e).__name__, "repr": repr(e)[:300], "hresult": hr, "where": where}


IEEE_FLOAT_GUID = "00000003-0000-0010-8000-00aa00389b71"


def pack_float_format(samplerate: int, channels: int) -> bytes:
    """표준 배치(1바이트 정렬, 40바이트) WAVEFORMATEXTENSIBLE: 32비트 float.

    soundcard 의 cffi 정의는 정렬 차이로 실제 구조보다 4바이트 뒤를 읽으므로(R5 changes.md) 쓰지 않습니다.
    """
    import struct
    import uuid

    mask = {1: 0x4, 2: 0x3}.get(channels, 0)  # 모노=앞 가운데, 스테레오=앞 좌우, 그 밖=지정 안 함
    head = struct.pack("<HHIIHHH", 0xFFFE, channels, samplerate, samplerate * channels * 4, channels * 4, 32, 22)
    return head + struct.pack("<HI", 32, mask) + uuid.UUID(IEEE_FLOAT_GUID).bytes_le


def parse_wave_format(raw: bytes) -> dict:
    """WAVEFORMATEX(18바이트) 또는 WAVEFORMATEXTENSIBLE(40바이트) 바이트 → 사전."""
    import struct
    import uuid

    tag, ch, sr, avg, align, bits, cb = struct.unpack("<HHIIHHH", raw[:18])
    d = {"tag": hex(tag), "channels": ch, "rate": sr, "bits": bits, "block_align": align, "cbSize": cb}
    if cb >= 22 and len(raw) >= 40:
        valid, mask = struct.unpack("<HI", raw[18:24])
        d.update(valid_bits=valid, channel_mask=hex(mask), subformat=str(uuid.UUID(bytes_le=raw[24:40])))
    return d


_LAST_OK: dict = {}  # 장치 id → 마지막으로 열린 방법(OpenCombo)


class _SoundcardSource:
    """soundcard 의 recorder 를 감싼 조각 읽기. 만든 스레드에서만 읽고 닫아야 합니다."""

    COMBOS = OPEN_COMBOS

    def __init__(self, mic_obj, mf=None):
        self.device_id = getattr(mic_obj, "id", None)
        try:
            self.device_name = str(mic_obj.name)  # 진단용(오류 내용 복사)
        except Exception:
            self.device_name = None
        self._mic = mic_obj
        self._mf = mf  # soundcard.mediafoundation(윈도우). 없으면 'direct' 방법은 건너뜀
        self.attempts: list[dict] = []
        self._mix: dict | None = None
        last_err = None
        combos = list(self.COMBOS)
        last = _LAST_OK.get(self.device_id)
        if last in combos:  # 다시 연결할 때는 마지막으로 열린 방법부터(감사 R5 N1)
            combos.remove(last)
            combos.insert(0, last)
        skip_sc = self._mix_not_extensible()
        for combo in combos:
            if skip_sc and combo.method == "soundcard":
                # 믹스 형식이 확장 형식이 아니면 soundcard 의 516·517줄 assert 로 실패하고, 그때 만든 COM 객체를
                # 돌려받을 수 없어 새므로(감사 R5 N1) 시도하지 않습니다. 열리던 장치는 확장 형식이라 해당 없음.
                self.attempts.append({"combo": combo.label(), "type": "건너뜀", "repr": f"믹스 형식 {skip_sc}",
                                      "hresult": None, "where": ""})
                continue
            rec = None
            try:
                rec, sr = self._open(combo)
                rec.__enter__()
            except Exception as e:
                self._release_failed(rec)
                info = describe_exception(e)
                info["combo"] = combo.label()
                self.attempts.append(info)
                last_err = e
                continue
            self.started_at = time.monotonic()  # Start() 직후
            self._rec = rec
            self.samplerate = int(sr)
            self.combo = combo.label()
            self._block = int(self.samplerate * BLOCK_SECONDS)
            if self.device_id is not None:
                _LAST_OK[self.device_id] = combo
            return
        err = DeviceOpenError(self.device_name, self.attempts, self.device_info())
        raise err from last_err

    # ------------------------------------------------------------ 여는 방법
    def _mix_not_extensible(self) -> str:
        """믹스 형식을 읽을 수 있고 확장 형식(태그 0xfffe, cbSize 22)이 아니면 그 설명, 아니면 ''."""
        if self._mf is None:
            return ""
        try:
            mix = self._mix_format()
        except Exception:
            return ""
        if mix and "tag" in mix and "cbSize" in mix and (mix["tag"] != "0xfffe" or mix["cbSize"] != 22):
            return f"태그 {mix.get('tag')}, cbSize {mix.get('cbSize')}"
        return ""

    def _release_failed(self, rec) -> None:
        """Initialize 뒤 __enter__ 에서 실패한 recorder 의 COM 객체를 돌려줍니다."""
        if rec is None or self._mf is None:
            return
        for attr in ("_ppCaptureClient", "_ptr"):
            ptr = getattr(rec, attr, None)
            if ptr is not None:
                try:
                    self._mf._com.release(ptr)
                except Exception:
                    pass

    def _mix_format(self) -> dict:
        """장치 믹스 형식(공유 모드). 한 번만 읽습니다."""
        if self._mix is None:
            self._mix = read_mix_format(self._mic, self._mf) if self._mf is not None else {}
        return self._mix

    def _open(self, combo: OpenCombo):
        if combo.method == "soundcard":
            sr = combo.samplerate or self._mix_format().get("rate") or 48000
            block = int(sr * combo.buffer_s) if combo.buffer_s else None
            return self._mic.recorder(samplerate=sr, channels=combo.channels, blocksize=block), sr
        if combo.method == "direct":
            if self._mf is None:
                raise NotImplementedError("direct 열기는 윈도우(soundcard.mediafoundation)에서만")
            mix = self._mix_format()
            sr = combo.samplerate or mix.get("rate") or 48000
            ch = combo.channels or mix.get("channels") or 1
            return make_direct_recorder(self._mic, self._mf, sr, ch, combo.buffer_s or WASAPI_BUFFER_SECONDS), sr
        raise ValueError(combo.method)

    def device_info(self) -> dict:
        """진단용 장치 속성(얻을 수 있는 것만, 실패는 실패 내용으로)."""
        info: dict = {"이름": self.device_name, "루프백": bool(getattr(self._mic, "isloopback", False))}
        try:
            info["채널(soundcard)"] = self._mic.channels
        except Exception as e:
            d = describe_exception(e)
            info["채널(soundcard)"] = f"확인 실패 {d['type']} {d['where']}"
        try:
            mix = self._mix_format()
            if mix:
                info["믹스 형식"] = mix
        except Exception as e:
            d = describe_exception(e)
            info["믹스 형식"] = f"확인 실패 {d['type']} {d['repr']} {d['hresult'] or ''}"
        return info

    def read(self) -> np.ndarray:
        data = self._rec.record(numframes=self._block)
        data = np.asarray(data, dtype=np.float32)
        if data.ndim == 2:
            data = data.mean(axis=1)  # 여러 채널로 열렸으면 모노로 합침
        return data

    def close(self) -> None:
        try:
            self._rec.__exit__(None, None, None)
        except Exception:
            pass


def read_mix_format(mic_obj, mf) -> dict:
    """IAudioClient::GetMixFormat 의 실제 바이트를 표준 배치로 읽습니다(윈도우)."""
    ptr = mic_obj._audio_client()
    try:
        pp = mf._ffi.new("WAVEFORMATEXTENSIBLE**")
        hr = ptr[0][0].lpVtbl.GetMixFormat(ptr[0], pp)
        mf._com.check_error(hr)
        try:
            raw = bytes(mf._ffi.buffer(pp[0], 18))
            cb = int.from_bytes(raw[16:18], "little")
            if cb >= 22:
                raw = bytes(mf._ffi.buffer(pp[0], 40))
            return parse_wave_format(raw)
        finally:
            mf._ole32.CoTaskMemFree(pp[0])
    finally:
        mf._com.release(ptr)


def make_direct_recorder(mic_obj, mf, samplerate: int, channels: int, buffer_s: float):
    """soundcard 의 _Recorder 를 쓰되, 초기화는 표준 배치 float 형식으로 직접 합니다(믹스 형식 assert 를 피함)."""
    ptr = mic_obj._audio_client()
    try:
        rec = mf._Recorder.__new__(mf._Recorder)
        rec._ptr = ptr
        rec.channelmap = list(range(channels))
        fmt = mf._ffi.new("char[]", pack_float_format(samplerate, channels))
        #            resample   | remix      | better-SRC | nopersist   (soundcard 와 같은 플래그)
        flags = 0x00100000 | 0x80000000 | 0x08000000 | 0x00080000
        if getattr(mic_obj, "isloopback", False):
            flags |= 0x00020000
        hr = ptr[0][0].lpVtbl.Initialize(ptr[0], 0, flags, int(buffer_s * 10_000_000), 0,
                                         mf._ffi.cast("WAVEFORMATEXTENSIBLE*", fmt), mf._ffi.NULL)
        mf._com.check_error(hr)
        rec._fmt_keepalive = fmt
        rec.samplerate = samplerate
        rec._idle_start_time = None
        return rec
    except BaseException:
        mf._com.release(ptr)
        raise


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

    def _mf(self):
        return sys.modules.get("soundcard.mediafoundation")

    def _open_mic(self, device_id: str | None) -> Source:
        mic = self._sc.default_microphone() if not device_id else self._sc.get_microphone(device_id)
        return _SoundcardSource(mic, self._mf())

    def _open_loop(self, speaker_id: str | None) -> Source:
        if not speaker_id:
            speaker_id = self._sc.default_speaker().id
        lb = self._sc.get_microphone(speaker_id, include_loopback=True)
        return _SoundcardSource(lb, self._mf())

    # 아래 넷은 녹음 스레드(COM 초기화됨)에서 직접 부릅니다.
    def open_microphone(self, device_id: str | None) -> Source:
        return self._open_mic(device_id)

    def open_loopback(self, speaker_id: str | None) -> Source:
        return self._open_loop(speaker_id)

    def default_microphone_id(self) -> str | None:
        return self._sc.default_microphone().id

    def default_speaker_id(self) -> str | None:
        return self._sc.default_speaker().id


def check_device(backend, kind: str, device_id: str | None, seconds: float = 1.0) -> dict:
    """녹음하지 않고 장치를 열어 seconds 동안 읽어 봅니다([장치 점검], 자체 시험용).

    녹음 스레드와 같은 규칙(F1): COM 을 초기화한 새 스레드에서 열고·읽고·닫습니다.
    결과: ok, 장치 이름, 열린 방법(combo), 주파수, 소리 크기(dBFS, 가장 큰 0.1초), 앞서 실패한 방법들,
    실패면 error·attempts·device_info.
    """
    def work():
        src = backend.open_microphone(device_id) if kind == "mic" else backend.open_loopback(device_id)
        try:
            peak, frames = -90.0, 0
            t = time.monotonic()
            while time.monotonic() - t < seconds:
                b = src.read()
                if b.size:
                    frames += len(b)
                    rms = float(np.sqrt(np.mean(np.square(b, dtype=np.float64))))
                    peak = max(peak, 20.0 * np.log10(rms + 1e-9))
            return {"ok": True, "kind": kind, "device": getattr(src, "device_name", None) or getattr(src, "device_id", None),
                    "combo": getattr(src, "combo", "?"), "samplerate": int(src.samplerate),
                    "frames": frames, "level_db": round(max(-90.0, peak), 1),
                    "failed_before": list(getattr(src, "attempts", []))}
        finally:
            src.close()

    try:
        return _run_in_mta(work, timeout=seconds + 30)
    except DeviceOpenError as e:
        return {"ok": False, "kind": kind, "device": e.device, "error": str(e), "attempts": e.attempts,
                "device_info": e.device_info}
    except Exception as e:
        d = describe_exception(e)
        return {"ok": False, "kind": kind, "device": device_id, "error": f"{d['type']}: {d['repr']}", "attempts": [d]}


def check_text(r: dict) -> str:
    """점검 결과 한 덩어리 글(화면·오류 복사용)."""
    who = "내 마이크" if r.get("kind") == "mic" else "상대방 소리(스피커)"
    if r.get("ok"):
        lv = r["level_db"]
        hint = "소리 들어옴" if lv > -55 else "조용함(말하거나 소리를 틀고 다시 점검해 보십시오)"
        extra = f" · 앞서 실패한 방법 {len(r['failed_before'])}개" if r.get("failed_before") else ""
        return f"{who}: 열림 — {r['device']} · 방법 {r['combo']} · {lv} dBFS({hint}){extra}"
    return f"{who}: 열지 못함\n{r.get('error', '')}"


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
        self.combo = "fake"

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
