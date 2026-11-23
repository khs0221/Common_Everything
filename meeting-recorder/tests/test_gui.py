"""화면 연기 시험: 가짜 장치로 녹음 시작·중지, 처리 없이 결과 폴더가 생기는지."""
import os
import time

import pytest

pytestmark = pytest.mark.gui


def test_gui_record_with_fake_devices(tmp_path, monkeypatch):
    monkeypatch.setenv("MREC_FAKE_DEVICES", "1")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    import tkinter as tk
    from tkinter import messagebox

    from mrec import gui

    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    root = tk.Tk()
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
        root.destroy()
