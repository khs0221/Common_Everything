"""화면 연기 시험: 가짜 장치로 녹음 시작·중지, 처리 없이 결과 폴더가 생기는지.

Tk 루트는 conftest 가 세션에서 한 번만 만들고(제품과 같음), 각 시험은 `tk_window`(Toplevel)에 화면을 올립니다.
"""
import os
import time

import pytest

pytestmark = pytest.mark.gui


def test_gui_record_with_fake_devices(tmp_path, monkeypatch, tk_window):
    monkeypatch.setenv("MREC_FAKE_DEVICES", "1")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from tkinter import messagebox

    from mrec import gui

    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    root = tk_window
    app = None
    try:
        app = gui.App(root)
        app.out_dir = tmp_path / "out"
        app.auto_var.set(False)
        assert app.mic_cb.get().endswith("시험용 마이크")
        app.start_recording()
        assert app.recorder.running and str(app.stop_btn["state"]) == "normal"
        t = time.monotonic()
        while time.monotonic() - t < 1.5:
            root.update()
            time.sleep(0.05)
        assert app.elapsed_var.get() >= "00:00:01"
        assert app.mic_bar["value"] > 0
        app.stop_recording()
        folders = list((tmp_path / "out").iterdir())
        assert len(folders) == 1
        assert (folders[0] / "마이크_나.wav").is_file() and (folders[0] / "스피커_상대방.wav").is_file()
        assert str(app.start_btn["state"]) == "normal"
    finally:
        if app is not None and app.recorder is not None and app.recorder.running:
            app.recorder.stop()  # 단언이 실패해도 녹음 스레드를 남기지 않음


def test_gui_shows_alarm_on_device_loss(tmp_path, monkeypatch, tk_window):
    """장치가 끊기면 빨간 경고가 뜨고, 다시 연결되면 사라집니다(F2)."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from tkinter import messagebox

    from mrec import gui
    from mrec.devices import FakeBackend, FakeSource, tone

    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)
    calls = {"n": 0}

    def loop():
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeSource(16000, tone(660, 0.2, 16000), fail_after_s=0.3)
        if calls["n"] == 2:
            raise OSError("없음")
        return FakeSource(16000, tone(660, 0.2, 16000))

    monkeypatch.setattr(gui, "default_backend", lambda: FakeBackend(loop=loop))
    root = tk_window
    app = None
    try:
        app = gui.App(root)
        app.out_dir = tmp_path / "out"
        app.auto_var.set(False)
        app.start_recording()
        seen, t = False, time.monotonic()
        while time.monotonic() - t < 2.8:
            root.update()
            seen = seen or bool(app.rec_alarm.get())
            time.sleep(0.05)
        assert seen
        assert app.rec_alarm.get() == ""  # 다시 연결됨
        app.stop_recording()
        assert "다시 연결" in app.status.get()
    finally:
        if app is not None and app.recorder is not None and app.recorder.running:
            app.recorder.stop()  # 단언이 실패해도 녹음 스레드를 남기지 않음
