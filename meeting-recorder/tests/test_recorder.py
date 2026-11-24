"""녹음 계층 시험(가짜 장치). 실제 윈도우 장치(WASAPI)는 여기서 검증하지 못합니다."""
import json
import threading
import time

import numpy as np
import soundfile as sf

from mrec.devices import FakeBackend, FakeSource, tone
from mrec.recorder import INFO_FILE, LAG_TOLERANCE_S, AlignedTrack, Recorder

SR = 16000


class _NullSource:
    samplerate = SR

    def read(self):
        return np.zeros(0, np.float32)

    def close(self):
        pass


def test_aligned_track_pads_gap_deterministic(tmp_path):
    """루프백이 1초 동안 데이터를 안 보내도, 다음 조각은 제 시각 위치에 써져야 합니다."""
    tr = AlignedTrack("loop", _NullSource(), tmp_path / "l.wav", t0=100.0, clock=lambda: 0, stop=threading.Event())
    blk = int(0.1 * SR)
    for k in range(1, 11):  # 0.0~1.0초
        tr.write_block(np.full(blk, 0.1, np.float32), 100.0 + k * 0.1)
    # 1.0~2.0초 무음(데이터 없음), 2.0~2.1초 조각 도착
    tr.write_block(np.full(blk, 0.5, np.float32), 102.1)
    tr.close()
    x, _ = sf.read(str(tmp_path / "l.wav"), dtype="float32")
    assert len(x) == int(2.1 * SR)
    assert np.all(np.abs(x[int(1.05 * SR):int(1.95 * SR)]) < 1e-4)
    assert np.all(np.abs(x[int(2.0 * SR) + 10:] - 0.5) < 1e-3)
    assert tr.stats.frames_padded == SR and tr.stats.pad_events == 1


def test_small_jitter_not_padded(tmp_path):
    tr = AlignedTrack("mic", _NullSource(), tmp_path / "m.wav", t0=0.0, clock=lambda: 0, stop=threading.Event())
    blk = int(0.1 * SR)
    t = 0.0
    for k in range(20):
        t += 0.1 + (0.1 if k % 2 else -0.05) * (LAG_TOLERANCE_S / 2)  # 허용 범위 안의 흔들림
        tr.write_block(np.zeros(blk, np.float32), t)
    tr.close()
    assert tr.stats.frames_padded == 0


def _clicky(source_holder, times, sr=SR):
    """절대 시각(time.monotonic) times 에 짧은 '딸깍' 을 넣는 신호."""

    def f(start, n):
        src = source_holder[0]
        t = src._start + (start + np.arange(n)) / sr
        y = np.zeros(n, np.float32)
        for c in times:
            y[(t >= c) & (t < c + 0.02)] = 0.8
        return y

    return f


def _first_click(path):
    x, sr = sf.read(str(path), dtype="float32")
    idx = np.flatnonzero(np.abs(x) > 0.5)
    return idx[0] / sr if len(idx) else None, len(x) / sr


def test_two_tracks_stay_aligned_with_gap_and_slow_clock(tmp_path):
    base = time.monotonic()
    click = base + 1.6
    mic_h, loop_h = [None], [None]

    def mk_mic():
        mic_h[0] = FakeSource(SR, _clicky(mic_h, [click]))
        return mic_h[0]

    def mk_loop():
        # 0.4~1.2초 데이터 없음(무음), 장치 시계 0.5% 느림
        loop_h[0] = FakeSource(SR, _clicky(loop_h, [click]), gaps=[(0.4, 1.2)], rate_error=-0.005)
        return loop_h[0]

    rec = Recorder(FakeBackend(mic=mk_mic, loop=mk_loop))
    folder = rec.start(tmp_path, None, None)
    time.sleep(2.4)
    res = rec.stop()
    c_mic, len_mic = _first_click(res.mic_path)
    c_loop, len_loop = _first_click(res.loop_path)
    assert c_mic is not None and c_loop is not None
    expected = click - rec.t0
    # 조각 0.1초 + 허용 지연 이내로 같은 위치
    assert abs(c_mic - c_loop) < 0.15, (c_mic, c_loop)
    assert abs(c_mic - expected) < 0.15
    assert abs(len_mic - len_loop) < 1e-3
    assert res.stats["loop"]["frames_padded"] > 0
    info = json.loads((folder / INFO_FILE).read_text(encoding="utf-8"))
    assert info["finished"] is True and info["seconds"] > 2.0


def test_device_failure_keeps_other_track_and_equal_length(tmp_path):
    backend = FakeBackend(mic=lambda: FakeSource(SR, tone(440, 0.2, SR), fail_after_s=0.5),
                          loop=lambda: FakeSource(SR, tone(660, 0.2, SR)))
    rec = Recorder(backend)
    rec.start(tmp_path, None, None)
    time.sleep(1.5)
    assert "mic" in rec.errors()
    res = rec.stop()
    n_mic = sf.info(str(res.mic_path)).frames
    n_loop = sf.info(str(res.loop_path)).frames
    assert n_mic == n_loop and n_loop >= int(1.4 * SR)
    assert res.stats["mic"]["error"]


def test_recording_survives_without_stop(tmp_path):
    """중지 버튼을 못 누르고 프로그램이 죽어도 그때까지의 녹음이 읽혀야 합니다."""
    rec = Recorder(FakeBackend())
    rec.start(tmp_path, None, None)
    time.sleep(1.2)
    # stop() 을 부르지 않고 파일을 읽어 봅니다.
    n_mic = sf.info(str(rec.folder / "마이크_나.wav")).frames
    n_loop = sf.info(str(rec.folder / "스피커_상대방.wav")).frames
    assert n_mic >= int(0.9 * SR) and n_loop >= int(0.5 * SR)
    rec.stop()


def test_open_failure_creates_no_folder(tmp_path):
    def bad():
        raise OSError("장치 없음")

    rec = Recorder(FakeBackend(loop=bad))
    try:
        rec.start(tmp_path, None, None)
    except OSError:
        pass
    assert list(tmp_path.iterdir()) == []
    assert not rec.running


def test_disk_write_failure_is_reported_and_stop_finishes(tmp_path):
    rec = Recorder(FakeBackend(loop=lambda: FakeSource(SR, tone(660, 0.2, SR))))
    rec.start(tmp_path, None, None)
    w = rec.tracks["mic"].writer
    orig = w.write
    calls = {"n": 0}

    def flaky(x):
        calls["n"] += 1
        if calls["n"] > 3:
            raise OSError(28, "No space left on device")
        return orig(x)

    w.write = flaky
    time.sleep(1.0)
    assert rec.errors()["mic"].startswith("디스크")
    t = time.monotonic()
    res = rec.stop()
    assert time.monotonic() - t < 5
    assert res.stats["loop"]["error"] is None
    assert sf.info(str(res.loop_path)).frames >= int(0.9 * SR)
