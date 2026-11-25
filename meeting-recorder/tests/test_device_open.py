"""장치 열기 물러나기와 진단(R5). 실제 WASAPI 없이 soundcard 를 흉내 낸 객체로 시험합니다."""
import numpy as np
import pytest

from mrec import devices
from mrec.devices import (OPEN_COMBOS, DeviceOpenError, FakeBackend, _SoundcardSource, check_device, check_text,
                          pack_float_format, parse_wave_format)


@pytest.fixture(autouse=True)
def _clear_last_ok():
    devices._LAST_OK.clear()
    yield
    devices._LAST_OK.clear()


class FakeRec:
    def __init__(self, sr, ch):
        self.sr, self.ch = sr, ch or 2
        self.entered = self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *a):
        self.exited = True

    def record(self, numframes):
        return np.full((numframes, self.ch), 0.1, np.float32)


class Mic:
    """soundcard _Microphone 흉내. fail(combo 인자) 가 True 면 문자열 없는 AssertionError 를 냅니다."""
    id = "usb-mic"
    name = "USB 헤드셋 마이크"
    isloopback = False

    def __init__(self, fail):
        self.fail = fail
        self.calls = []

    @property
    def channels(self):
        raise AssertionError()  # soundcard 406줄 assert 흉내

    def recorder(self, samplerate, channels=None, blocksize=None):
        self.calls.append((samplerate, channels, blocksize))
        if self.fail(samplerate, channels, blocksize):
            raise AssertionError()  # 516줄 assert 처럼 메시지 없는 예외
        return FakeRec(samplerate, channels)


def test_working_device_opens_with_first_combo_unchanged():
    m = Mic(lambda sr, ch, b: False)
    src = _SoundcardSource(m)
    assert src.combo == OPEN_COMBOS[0].label() and src.samplerate == 16000 and src.attempts == []
    assert m.calls == [(16000, 1, 8000)]  # R4 까지와 같은 첫 시도(16kHz, 모노, 0.5초 버퍼)
    assert src.read().shape == (1600,)


def test_soundcard_fails_all_then_direct_path_opens(monkeypatch):
    m = Mic(lambda sr, ch, b: True)
    monkeypatch.setattr(devices, "read_mix_format", lambda mic, mf: {"tag": "0x3", "channels": 1, "rate": 48000})
    made = []

    def direct(mic, mf, sr, ch, buf):
        made.append((sr, ch, buf))
        return FakeRec(sr, ch)

    monkeypatch.setattr(devices, "make_direct_recorder", direct)
    src = _SoundcardSource(m, mf=object())
    assert src.combo.startswith("direct/16000Hz/1ch") and src.samplerate == 16000
    assert len(src.attempts) == 3 and all(a["type"] == "AssertionError" for a in src.attempts)
    assert all("test_device_open.py" in a["where"] for a in src.attempts)  # 빈 메시지라도 위치가 남음
    assert made == [(16000, 1, 0.5)]


def test_all_fail_gives_combo_level_diagnosis():
    m = Mic(lambda sr, ch, b: True)
    with pytest.raises(DeviceOpenError) as ei:
        _SoundcardSource(m)  # mf 없음: direct 는 NotImplementedError
    e = ei.value
    assert len(e.attempts) == len(OPEN_COMBOS)
    text = str(e)
    assert "AssertionError AssertionError()" in text and "NotImplementedError" in text
    assert "soundcard/16000Hz/1ch/버퍼0.5s" in text and "@ test_device_open.py" in text
    assert "채널(soundcard)=확인 실패 AssertionError" in text and "USB 헤드셋 마이크" in text
    assert isinstance(e.__cause__, Exception)  # 원인 예외 연결(from)


def test_device_channels_combo_downmixes(monkeypatch):
    m = Mic(lambda sr, ch, b: ch == 1)  # 모노 요청만 실패
    monkeypatch.setattr(devices, "read_mix_format", lambda mic, mf: {"channels": 2, "rate": 44100})
    monkeypatch.setattr(devices, "make_direct_recorder", lambda *a: (_ for _ in ()).throw(OSError("x")))
    src = _SoundcardSource(m, mf=object())
    assert src.combo == "soundcard/48000Hz/장치값ch/버퍼기본"
    assert src.read().shape == (4800,)  # 2채널을 모노로 합침


def test_pack_float_format_is_standard_40_bytes():
    raw = pack_float_format(16000, 1)
    assert len(raw) == 40
    d = parse_wave_format(raw)
    assert d["tag"] == "0xfffe" and d["channels"] == 1 and d["rate"] == 16000 and d["bits"] == 32
    assert d["cbSize"] == 22 and d["valid_bits"] == 32 and d["channel_mask"] == "0x4"
    assert d["subformat"] == devices.IEEE_FLOAT_GUID
    assert parse_wave_format(raw[:18])["cbSize"] == 22  # 18바이트만 있어도 앞부분은 읽힘


def test_soundcard_cffi_layout_reads_subformat_4_bytes_late():
    """soundcard 의 정의(정렬 없음)는 SubFormat 을 28바이트에서 읽는다 → 516~525줄 assert 해석 근거."""
    cffi = pytest.importorskip("cffi")
    f = cffi.FFI()
    f.cdef("""typedef uint16_t WORD; typedef uint32_t DWORD;
    typedef struct { DWORD Data1; WORD Data2; WORD Data3; unsigned char Data4[8]; } GUID;
    typedef struct { WORD wFormatTag; WORD nChannels; DWORD nSamplesPerSec; DWORD nAvgBytesPerSec;
                     WORD nBlockAlign; WORD wBitsPerSample; WORD cbSize; } WAVEFORMATEX;
    typedef struct { WAVEFORMATEX Format; union { WORD wValidBitsPerSample; } Samples; DWORD dwChannelMask;
                     GUID SubFormat; } WAVEFORMATEXTENSIBLE;""")
    assert f.offsetof("WAVEFORMATEXTENSIBLE", "SubFormat") == 28  # 실제 배치는 24
    ba = bytearray(pack_float_format(48000, 1) + b"\0\0\0\0")
    buf = f.from_buffer(ba)
    p = f.cast("WAVEFORMATEXTENSIBLE*", buf)
    assert p.SubFormat.Data1 == 0x100000 and p.SubFormat.Data2 == 0x80  # soundcard 가 '경험으로' 맞춘 값


def test_check_device_ok_and_failure():
    r = check_device(FakeBackend(), "mic", None, seconds=0.3)
    assert r["ok"] and r["combo"] == "fake" and r["level_db"] > -60 and "열림" in check_text(r)

    class Bad(FakeBackend):
        def open_microphone(self, device_id):
            raise DeviceOpenError("USB 헤드셋 마이크", [{"combo": "c", "type": "AssertionError", "repr": "AssertionError()",
                                                     "hresult": None, "where": "x.py:1 f"}], {"채널": 1})

    r = check_device(Bad(), "mic", "id", seconds=0.2)
    assert not r["ok"] and r["attempts"][0]["type"] == "AssertionError"
    assert "열지 못함" in check_text(r) and "AssertionError()" in check_text(r)


def test_non_extensible_mix_skips_soundcard_and_reconnect_starts_with_last_ok(monkeypatch):
    """R6(감사 R5 N1): 확장 형식이 아니면 새는 soundcard 경로를 건너뛰고, 다시 열 때는 마지막 성공 방법부터."""
    devices._LAST_OK.clear()
    m = Mic(lambda sr, ch, b: True)
    monkeypatch.setattr(devices, "read_mix_format", lambda mic, mf: {"tag": "0x3", "cbSize": 0, "channels": 1, "rate": 48000})
    made = []
    monkeypatch.setattr(devices, "make_direct_recorder", lambda mic, mf, sr, ch, buf: made.append(sr) or FakeRec(sr, ch))
    src = _SoundcardSource(m, mf=object())
    assert m.calls == []  # soundcard recorder() 를 부르지 않음
    assert src.combo.startswith("direct/16000Hz") and src.attempts[0]["type"] == "건너뜀"
    m2 = Mic(lambda sr, ch, b: False)  # 같은 장치 id, 이번에는 soundcard 도 될 수 있어도
    monkeypatch.setattr(devices, "read_mix_format", lambda mic, mf: {"tag": "0xfffe", "cbSize": 22, "channels": 1, "rate": 48000})
    src2 = _SoundcardSource(m2, mf=object())
    assert src2.combo.startswith("direct/16000Hz") and m2.calls == []  # 마지막 성공 방법부터
    devices._LAST_OK.clear()
