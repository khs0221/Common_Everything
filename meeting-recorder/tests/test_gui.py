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
        assert app.mic_cb.get() == gui.AUTO_DEVICE  # 기본값은 '자동'
        assert "(현재 기본) 시험용 마이크" in app.mic_cb["values"]
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


def _app(tk_window, tmp_path, monkeypatch, backend_factory):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    from tkinter import messagebox

    from mrec import gui

    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)
    monkeypatch.setattr(gui, "default_backend", backend_factory)
    app = gui.App(tk_window)
    app.out_dir = tmp_path / "out"
    app.auto_var.set(False)
    return app


def test_gui_backend_failure_is_shown_and_refresh_retries(tmp_path, monkeypatch, tk_window):
    """장치 계층을 못 열면 원인 문구와 [오류 내용 복사]가 보이고, 새로고침이 만들기부터 다시 시도합니다(R2)."""
    from mrec import gui
    from mrec.devices import FakeBackend

    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Error 0x100000001")
        return FakeBackend()

    app = _app(tk_window, tmp_path, monkeypatch, factory)
    tk_window.update()
    assert app.backend is None and str(app.start_btn["state"]) == "disabled"
    assert "시작하지 못했습니다" in app.dev_err.get() and app.copy_btn.winfo_ismapped()
    app.copy_error()
    clip = tk_window.clipboard_get()
    assert "Error 0x100000001" in clip and "회의 녹음·기록" in clip and "윈도우/OS:" in clip
    app.refresh_devices()
    tk_window.update()
    assert calls["n"] == 2 and app.backend is not None
    assert app.dev_err.get() == "" and not app.copy_btn.winfo_ismapped()
    assert str(app.start_btn["state"]) == "normal"
    assert app.mic_cb.get() == gui.AUTO_DEVICE and app.spk_cb.get() == gui.AUTO_DEVICE


def test_gui_auto_records_even_when_device_list_fails(tmp_path, monkeypatch, tk_window):
    """장치 목록을 못 읽어도 '자동'으로 녹음이 시작됩니다(R2 완료 기준)."""
    from mrec import gui
    from mrec.devices import FakeBackend

    class NoList(FakeBackend):
        def list_microphones(self):
            raise RuntimeError("목록 실패")

        list_speakers = list_microphones

    backend = NoList()
    app = _app(tk_window, tmp_path, monkeypatch, lambda: backend)
    try:
        assert "자동'으로는 그대로 녹음" in app.dev_err.get()
        assert app.mic_cb.get() == gui.AUTO_DEVICE and str(app.start_btn["state"]) == "normal"
        app.start_recording()
        assert app.recorder is not None and app.recorder.running
        t = time.monotonic()
        while time.monotonic() - t < 0.6:
            tk_window.update()
            time.sleep(0.05)
        app.stop_recording()
        assert sorted(o[1] for o in backend.opened) == ["fake-mic", "fake-spk"]  # 기본 장치를 직접 엶
        assert app.settings["mic_id"] is None and app.settings["speaker_id"] is None
    finally:
        if app.recorder is not None and app.recorder.running:
            app.recorder.stop()


def test_gui_saved_device_and_auto_selection(tmp_path, monkeypatch, tk_window):
    """저장된 설정 호환: 목록에 있는 장치 id 는 그 장치, 없는 id·None 은 '자동'. 현재 기본 장치를 골라도 고정(N3)."""
    import json

    from mrec import gui
    from mrec.devices import DeviceInfo, FakeBackend

    class Two(FakeBackend):
        def list_microphones(self):
            return [DeviceInfo("m-default", "기본 마이크", True), DeviceInfo("m-usb", "USB 마이크")]

        def list_speakers(self):
            return [DeviceInfo("s-default", "기본 스피커", True)]

    appdata = tmp_path / "appdata"
    (appdata / "MeetingRecorder").mkdir(parents=True)
    (appdata / "MeetingRecorder" / "settings.json").write_text(
        json.dumps({"mic_id": "m-usb", "speaker_id": "gone-device"}), encoding="utf-8")
    app = _app(tk_window, tmp_path, monkeypatch, Two)
    assert app.mic_cb.get() == "USB 마이크"
    assert app.spk_cb.get() == gui.AUTO_DEVICE  # 없는 장치 → 자동
    assert app._selected(app.mic_cb, app.mics) == "m-usb"
    assert app._selected(app.spk_cb, app.spks) is None
    app.mic_cb.current(1)  # '(현재 기본) 기본 마이크' 를 직접 고름 → 그 장치로 고정
    assert app.mic_cb.get().startswith("(현재 기본)")
    assert app._selected(app.mic_cb, app.mics) == "m-default"


def test_gui_device_check_button(tmp_path, monkeypatch, tk_window):
    """[장치 점검]: 열리면 결과 안내, 못 열면 조합별 진단을 오류 칸과 복사 내용에 보여 줍니다(R5)."""
    from tkinter import messagebox

    from mrec import gui
    from mrec.devices import DeviceOpenError, FakeBackend

    shown = {}
    monkeypatch.setattr(messagebox, "showinfo", lambda t, m, **k: shown.setdefault("info", m))
    monkeypatch.setattr(messagebox, "showwarning", lambda t, m, **k: shown.setdefault("warn", m))

    class Bad(FakeBackend):
        def open_microphone(self, device_id):
            raise DeviceOpenError("USB 헤드셋 마이크", [{"combo": "soundcard/16000Hz/1ch/버퍼0.5s", "type": "AssertionError",
                                                     "repr": "AssertionError()", "hresult": None,
                                                     "where": "mediafoundation.py:516 __init__"}], {"채널": 1})

    for backend, ok in ((FakeBackend(), True), (Bad(), False)):
        shown.clear()
        app = _app(tk_window, tmp_path, monkeypatch, lambda b=backend: b)
        app.check_devices()
        t = time.monotonic()
        while time.monotonic() - t < 10 and not shown:
            tk_window.update()
            time.sleep(0.05)
        if ok:
            assert "내 마이크: 열림" in shown["info"] and "방법 fake" in shown["info"]
        else:
            assert "열지 못함" in shown["warn"] and "mediafoundation.py:516" in app._err_report
            assert app.copy_btn.winfo_ismapped()
        assert str(app.check_btn["state"]) == "normal"


def test_gui_pause_resume_meeting_terms_mic_cut_and_12_speakers(tmp_path, monkeypatch, tk_window):
    """GUI 일시정지/재개 버튼, 이번 회의 용어, 마이크 소음 차단 선택, 상대방 인원 12명 확장."""
    from mrec import gui
    from mrec.devices import FakeBackend

    app = _app(tk_window, tmp_path, monkeypatch, FakeBackend)
    try:
        # 상대방 인원 12명 확장
        assert len(gui.SPK_CHOICES) == 13 and gui.SPK_CHOICES[-1] == ("12명", 12)
        assert "12명" in app.spk_n_cb["values"]

        # 마이크 소음 차단 기본 -40 dB, 선택 가능
        assert app._mic_min_db() == -40.0
        app.mic_cut_cb.current(0)
        assert app._mic_min_db() is None
        app.mic_cut_cb.current(3)
        assert app._mic_min_db() == -30.0

        # 이번 회의 용어 입력 칸
        app.meeting_terms_var.set("ProjectX, 홍길동; BMS")
        assert app._meeting_terms() == ["ProjectX", "홍길동", "BMS"]

        # 녹음 시작 → 일시정지 → 재개 → 중지
        assert str(app.pause_btn["state"]) == "disabled"
        app.start_recording()
        assert str(app.pause_btn["state"]) == "normal"
        app.toggle_pause()
        assert app.recorder.paused and "재개" in app.pause_btn.cget("text")
        tk_window.update()
        app.toggle_pause()
        assert not app.recorder.paused and "일시정지" in app.pause_btn.cget("text")
        app.stop_recording()
        assert str(app.pause_btn["state"]) == "disabled"
    finally:
        if app.recorder is not None and app.recorder.running:
            app.recorder.stop()


def test_names_dialog_scrollable_for_many_speakers(tmp_path, tk_window):
    """참석자가 많아도(12명+) NamesDialog 가 스크롤 가능해야 합니다."""
    import tkinter as tk

    from mrec import gui
    from mrec.merge import ME, Utterance

    utts = [Utterance(0.0, 1.0, ME, "hi")] + [
        Utterance(i * 2.0, i * 2.0 + 1.0, f"spk{i}", f"speaker {i} line") for i in range(1, 13)
    ]
    status = tk.StringVar(value="")
    dlg = gui.NamesDialog(tk_window, tmp_path, utts, {}, status)
    try:
        tk_window.update_idletasks()
        assert len(dlg.entries) == 13
        assert dlg.canvas is not None and dlg.scrollbar is not None
        assert int(float(dlg.canvas.cget("height"))) <= gui.NamesDialog.MAX_BODY_HEIGHT
    finally:
        dlg.top.destroy()

