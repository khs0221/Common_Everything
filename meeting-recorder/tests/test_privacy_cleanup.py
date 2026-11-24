"""R3 감사 이월 건: E1(사용자 폴더 경로 가리기), E3(시작 실패 때 빈 폴더 정리)."""
import logging
import threading

import pytest

from mrec import sysinfo
from mrec.devices import FakeBackend, FakeSource, tone
from mrec.recorder import Recorder

SR = 16000


def test_mask_user_paths_windows_and_home():
    m = sysinfo.mask_user_paths
    assert m(r'File "C:\Users\홍길동\Downloads\x.py"') == r'File "C:\Users\<사용자>\Downloads\x.py"'
    assert m("C:/Users/kim.lee/Documents/a") == "C:/Users/<사용자>/Documents/a"
    assert m(r"c:\\users\\Bob\\x") == r"c:\\users\\<사용자>\\x"  # repr 처럼 두 번 쓴 역슬래시
    assert m("/home/alice/x") == "/home/<사용자>/x"
    assert m(r"D:\data\meeting") == r"D:\data\meeting"  # 사용자 폴더가 아니면 그대로


def test_log_formatter_masks_paths_in_traceback():
    fmt = sysinfo.MaskingFormatter("%(message)s")
    try:
        raise OSError(r"C:\Users\홍길동\AppData\x 를 열 수 없음")
    except OSError:
        import sys

        rec = logging.LogRecord("mrec", logging.ERROR, __file__, 1, r"경로 C:\Users\홍길동\문서", (), sys.exc_info())
    out = fmt.format(rec)
    assert "홍길동" not in out and "<사용자>" in out


@pytest.mark.gui  # mrec.gui 를 불러오므로 tkinter 필요
def test_error_report_is_masked(monkeypatch, tmp_path):
    from mrec import gui

    logd = tmp_path / "logs"
    logd.mkdir()
    (logd / "app.log").write_text(r"오류 C:\Users\홍길동\Documents\회의기록" + "\n", encoding="utf-8")
    monkeypatch.setattr(sysinfo, "log_dir", lambda: logd)
    text = gui.error_report("시험", r"RuntimeError: C:\Users\홍길동\x")
    assert "홍길동" not in text and text.count("<사용자>") >= 2


def test_start_failure_removes_empty_folder(tmp_path, monkeypatch):
    """스피커 파일 준비가 실패하면 빈 회의 폴더와 머리말만 있는 wav 를 남기지 않습니다(E3)."""
    from mrec import recorder as recmod

    orig = recmod.AlignedTrack.attach_writer

    def attach(self, path, t0):
        if self.name == "loop":
            raise OSError(28, "No space left on device")
        return orig(self, path, t0)

    monkeypatch.setattr(recmod.AlignedTrack, "attach_writer", attach)
    rec = Recorder(FakeBackend(mic=lambda: FakeSource(SR, tone(440, 0.1, SR)),
                               loop=lambda: FakeSource(SR, tone(660, 0.1, SR))))
    try:
        rec.start(tmp_path, None, None)
        raise AssertionError("예외가 나야 합니다")
    except OSError:
        pass
    assert list(tmp_path.iterdir()) == []
    assert not any(t.name.startswith("rec-") and t.is_alive() for t in threading.enumerate())


def test_partial_folder_with_other_files_is_kept(tmp_path):
    rec = Recorder(FakeBackend())
    folder = tmp_path / "회의_x"
    folder.mkdir()
    (folder / "메모.txt").write_text("사용자 파일", encoding="utf-8")
    rec.folder = folder
    rec._remove_partial_folder()
    assert (folder / "메모.txt").is_file()
