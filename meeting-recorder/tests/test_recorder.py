"""녹음 계층 시험(가짜 장치). 실제 윈도우 장치(WASAPI)는 여기서 검증하지 못합니다."""
import json
import threading
import time

import numpy as np
import soundfile as sf

from mrec.devices import FakeBackend, FakeSource, tone
from mrec.recorder import INFO_FILE, LAG_TOLERANCE_S, STOP_JOIN_S, AlignedTrack, Recorder

SR = 16000


def _track(tmp_path, name, t0):
    return AlignedTrack(name, lambda: None, clock=lambda: 0, stop=threading.Event(),
                        path=tmp_path / f"{name}.wav", t0=t0, samplerate=SR)


def test_aligned_track_pads_gap_deterministic(tmp_path):
    """루프백이 1초 동안 데이터를 안 보내도, 다음 조각은 제 시각 위치에 써져야 합니다."""
    tr = _track(tmp_path, "l", 100.0)
    blk = int(0.1 * SR)
    for k in range(1, 11):  # 0.0~1.0초
        tr.write_block(np.full(blk, 0.1, np.float32), 100.0 + k * 0.1)
    # 1.0~2.0초 무음(데이터 없음), 2.0~2.1초 조각 도착
    tr.write_block(np.full(blk, 0.5, np.float32), 102.1)
    tr.close()
    x, _ = sf.read(str(tmp_path / "l.wav"), dtype="float32")  # noqa
    assert len(x) == int(2.1 * SR)
    assert np.all(np.abs(x[int(1.05 * SR):int(1.95 * SR)]) < 1e-4)
    assert np.all(np.abs(x[int(2.0 * SR) + 10:] - 0.5) < 1e-3)
    assert tr.stats.frames_padded == SR and tr.stats.pad_events == 1


def test_small_jitter_not_padded(tmp_path):
    tr = _track(tmp_path, "m", 0.0)
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
        # 0.3~1.3초 장치가 멈춰 데이터가 오지 않음(버퍼 넘침), 장치 시계 0.5% 느림
        loop_h[0] = FakeSource(SR, _clicky(loop_h, [click]), gaps=[(0.3, 1.3)], gap_mode="stall",
                               rate_error=-0.005)
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


def test_device_failure_reconnects_and_keeps_alignment(tmp_path):
    """장치가 끊기면 다시 연결하고, 끊긴 동안은 무음으로 채워 시간축을 유지합니다(F2)."""
    base = time.monotonic()
    click = base + 2.6
    calls = {"n": 0}
    mic_h, cur = [None], [None]

    def mk_mic():
        mic_h[0] = FakeSource(SR, _clicky(mic_h, [click]))
        return mic_h[0]

    def mk_loop():
        calls["n"] += 1
        if calls["n"] == 1:
            src = FakeSource(SR, tone(660, 0.2, SR), fail_after_s=0.6)  # 0.6초 뒤 뽑힘
        elif calls["n"] == 2:
            raise OSError("아직 장치 없음")
        else:
            src = FakeSource(SR, _clicky(cur, [click]))
        cur[0] = src
        return src

    rec = Recorder(FakeBackend(mic=mk_mic, loop=mk_loop))
    rec.start(tmp_path, None, None)
    seen_problem = False
    t = time.monotonic()
    while time.monotonic() - t < 3.4:
        seen_problem = seen_problem or "loop" in rec.problems()
        time.sleep(0.05)
    res = rec.stop()
    assert seen_problem
    assert res.stats["loop"]["reconnects"] == 1 and calls["n"] == 3
    assert not rec.problems()
    c_mic, len_mic = _first_click(res.mic_path)
    c_loop, len_loop = _first_click(res.loop_path)
    assert c_loop is not None and abs(c_mic - c_loop) < 0.15, (c_mic, c_loop)
    assert abs(len_mic - len_loop) < 1e-3


def test_default_device_change_reopens(tmp_path):
    backend = FakeBackend()
    rec = Recorder(backend)
    rec.start(tmp_path, None, None)  # None = 기본 장치 따라가기
    time.sleep(0.5)
    backend.default_spk = "new-headset"
    time.sleep(2.8)
    res = rec.stop()
    assert res.stats["loop"]["default_changes"] == 1
    assert [o[1] for o in backend.opened if o[0] == "loop"] == ["fake-spk", "new-headset"]
    assert res.stats["mic"]["default_changes"] == 0


def test_fixed_device_does_not_follow_default(tmp_path):
    backend = FakeBackend()
    rec = Recorder(backend)
    rec.start(tmp_path, "fake-mic", "fake-spk")
    time.sleep(0.3)
    backend.default_spk = "other"
    time.sleep(2.3)
    res = rec.stop()
    assert res.stats["loop"]["default_changes"] == 0


def test_open_delay_difference_is_compensated(tmp_path):
    """루프백이 0.4초 늦게 열려도 같은 순간의 소리가 두 파일의 같은 위치에 있어야 합니다(F3)."""
    click = time.monotonic() + 1.5
    mh, lh = [None], [None]

    def mk_mic():
        mh[0] = FakeSource(SR, _clicky(mh, [click]))
        return mh[0]

    def mk_loop():
        lh[0] = FakeSource(SR, _clicky(lh, [click]), open_delay_s=0.4)
        return lh[0]

    rec = Recorder(FakeBackend(mic=mk_mic, loop=mk_loop))
    rec.start(tmp_path, None, None)
    time.sleep(2.2)
    res = rec.stop()
    c_mic, _ = _first_click(res.mic_path)
    c_loop, _ = _first_click(res.loop_path)
    assert abs(c_mic - c_loop) < 0.05, (c_mic, c_loop)
    assert abs(c_mic - (click - rec.t0)) < 0.05
    assert abs(res.stats["loop"]["lead_in_frames"] / SR - 0.4) < 0.05


def test_open_read_close_on_same_thread(tmp_path):
    """장치를 여는 스레드와 읽고 닫는 스레드가 같아야 합니다(F1)."""
    backend = FakeBackend()
    rec = Recorder(backend)
    rec.start(tmp_path, None, None)
    srcs = {k: t.source for k, t in rec.tracks.items()}
    time.sleep(0.4)
    rec.stop()
    for kind, _, opened_thread in backend.opened:
        assert srcs[kind].reader_threads == {opened_thread}
        assert opened_thread != threading.get_ident()


def test_stop_during_stall_is_fast_and_cleans_up(tmp_path):
    """스피커 쪽이 멈춘(데이터 안 옴) 상태에서 중지해도 바로 끝나고 장치가 닫혀야 합니다(F5)."""
    rec = Recorder(FakeBackend(loop=lambda: FakeSource(SR, lambda s, n: np.full(n, 0.1, np.float32),
                                                       gaps=[(0.5, 100.0)], gap_mode="stall")))
    rec.start(tmp_path, None, None)
    time.sleep(1.2)
    src = rec.tracks["loop"].source
    t = time.monotonic()
    res = rec.stop()
    took = time.monotonic() - t
    # '멈춘 읽기를 기다리지 않았다' 를 직접 확인: 읽기 스레드 join 이 시간 초과 없이 끝났어야 함.
    # (기다렸다면 트랙마다 STOP_JOIN_S 를 다 쓰고 reader_alive_after_stop 이 True 가 됩니다.)
    assert not res.stats["loop"]["reader_alive_after_stop"] and not res.stats["mic"]["reader_alive_after_stop"]
    # 시간 기준은 '기다림(트랙당 STOP_JOIN_S)' 과 구별될 만큼만 둡니다. 파일 마무리(fsync)는 부하 걸린
    # 공용 CI 에서 0.5초를 넘을 수 있어(실측 0.59초) 시간 자체를 빡빡하게 재지 않습니다.
    assert took < STOP_JOIN_S, took
    assert not rec.tracks["loop"].thread.is_alive() and not rec.tracks["mic"].thread.is_alive()
    assert src._closed.is_set()
    assert sf.info(str(res.loop_path)).frames == sf.info(str(res.mic_path)).frames


def test_silence_returns_zeros_like_soundcard(tmp_path):
    """기본 가짜 장치는 실제 soundcard 처럼 조용할 때 0 을 돌려줍니다(무음 채움이 필요 없음)."""
    rec = Recorder(FakeBackend(loop=lambda: FakeSource(SR, tone(660, 0.2, SR), gaps=[(0.2, 0.9)])))
    rec.start(tmp_path, None, None)
    time.sleep(1.3)
    res = rec.stop()
    assert res.stats["loop"]["pad_events"] == 0
    x, _ = sf.read(str(res.loop_path), dtype="float32")
    assert np.abs(x[int(0.35 * SR):int(0.75 * SR)]).max() < 1e-3


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

    made = []

    def mic():
        made.append(FakeSource(SR, tone(440, 0.1, SR)))
        return made[-1]

    rec = Recorder(FakeBackend(mic=mic, loop=bad))
    try:
        rec.start(tmp_path, None, None)
        raise AssertionError("예외가 나야 합니다")
    except OSError:
        pass
    assert list(tmp_path.iterdir()) == []
    assert not rec.running
    assert made and made[0]._closed.is_set()  # 먼저 열린 마이크는 닫힘


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


def test_start_failure_after_open_stops_threads(tmp_path, monkeypatch):
    """장치는 열렸는데 폴더·파일 준비가 실패하면 녹음 스레드를 멈추고 장치를 닫아야 합니다(감사 N1)."""
    from mrec import recorder as recmod

    def no_space(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(recmod, "new_meeting_folder", no_space)
    made = []

    def mk(freq):
        def f():
            made.append(FakeSource(SR, tone(freq, 0.1, SR)))
            return made[-1]
        return f

    rec = Recorder(FakeBackend(mic=mk(440), loop=mk(660)))
    before = threading.active_count()
    try:
        rec.start(tmp_path, None, None)
        raise AssertionError("예외가 나야 합니다")
    except OSError:
        pass
    time.sleep(0.2)
    assert not rec.running and rec.tracks == {}
    assert len(made) == 2 and all(s._closed.is_set() for s in made)
    assert threading.active_count() <= before


def test_pause_resume_excludes_paused_duration_and_keeps_alignment(tmp_path):
    """일시정지 구간은 녹음 길이에서 제외되고, 재개 후에도 두 트랙 시간축이 맞아야 합니다."""
    base = time.monotonic()
    click_after = base + 1.9  # 0.6초 녹음 + 0.9초 일시정지 + 재개 후 0.4초 지점
    mic_h, loop_h = [None], [None]

    def mk_mic():
        mic_h[0] = FakeSource(SR, _clicky(mic_h, [click_after]))
        return mic_h[0]

    def mk_loop():
        loop_h[0] = FakeSource(SR, _clicky(loop_h, [click_after]))
        return loop_h[0]

    rec = Recorder(FakeBackend(mic=mk_mic, loop=mk_loop))
    folder = rec.start(tmp_path, None, None)
    time.sleep(0.6)
    rec.pause()
    assert rec.paused
    el_paused = rec.elapsed()
    time.sleep(0.9)
    assert abs(rec.elapsed() - el_paused) < 0.02  # 일시정지 중 경과 시간 정지
    assert all(v == -90.0 for v in rec.levels().values())
    rec.resume()
    assert not rec.paused
    time.sleep(0.8)
    res = rec.stop()
    # 전체 벽시계 ~2.3초 중 0.9초 일시정지 제외 → 약 1.4초
    assert 1.0 < res.seconds < 1.9, res.seconds
    c_mic, len_mic = _first_click(res.mic_path)
    c_loop, len_loop = _first_click(res.loop_path)
    assert c_mic is not None and c_loop is not None
    assert abs(c_mic - c_loop) < 0.15, (c_mic, c_loop)
    assert abs(len_mic - len_loop) < 1e-3
    info = json.loads((folder / INFO_FILE).read_text(encoding="utf-8"))
    assert info["paused_seconds"] >= 0.8


def test_stop_while_paused_excludes_paused_duration(tmp_path):
    rec = Recorder(FakeBackend())
    rec.start(tmp_path, None, None)
    time.sleep(0.5)
    rec.pause()
    time.sleep(0.8)
    res = rec.stop()
    assert not rec.paused and not rec.running
    assert res.seconds < 0.9, res.seconds

