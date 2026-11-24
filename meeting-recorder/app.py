"""회의 녹음·기록 프로그램 시작점.

  MeetingRecorder.exe                 화면 열기
  MeetingRecorder.exe --selftest      장치 없이 자체 시험(결과: --selftest-out 파일)
  MeetingRecorder.exe --fake-devices  가짜 장치로 화면 열기(개발·시연용)
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import sys


def _fix_std_streams() -> None:
    # 창 모드 exe 에는 콘솔이 없어 sys.stdout/stderr 가 None 입니다. 라이브러리가 출력하다
    # 멈추지 않게 빈 출력으로 바꿉니다.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    from mrec import offline

    offline.enable()  # 가장 먼저: 실행 중 외부 접속 차단
    p = argparse.ArgumentParser(prog="MeetingRecorder")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--selftest-out", default=None)
    p.add_argument("--fake-devices", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        had_console = sys.stdout is not None
        _fix_std_streams()
        out = args.selftest_out
        if not out and not had_console:
            out = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "selftest_result.json")
        try:
            from mrec import selftest

            return selftest.run(out)
        except BaseException:  # 창 모드에서 오류 창이 떠 CI 가 멈추지 않게, 파일로 남기고 끝냄
            import json
            import traceback

            if out:
                with open(out, "w", encoding="utf-8") as f:
                    json.dump({"ok": False, "fatal": traceback.format_exc()[-4000:]}, f, ensure_ascii=False)
            return 2
    _fix_std_streams()
    if args.fake_devices:
        os.environ["MREC_FAKE_DEVICES"] = "1"
    try:
        from mrec import gui

        return gui.main()
    except Exception:
        import logging
        import traceback

        logging.getLogger("mrec").exception("예상하지 못한 오류")
        try:
            from tkinter import messagebox

            messagebox.showerror("회의 녹음·기록", "예상하지 못한 오류로 프로그램을 닫습니다.\n"
                                 "녹음 파일은 저장 폴더에 남아 있습니다.\n\n" + traceback.format_exc()[-800:])
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
