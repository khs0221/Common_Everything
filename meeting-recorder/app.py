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
        from mrec import selftest

        out = args.selftest_out
        if not out and not had_console:
            from mrec.paths import app_dir

            out = str(app_dir() / "selftest_result.json")
        return selftest.run(out)
    _fix_std_streams()
    if args.fake_devices:
        os.environ["MREC_FAKE_DEVICES"] = "1"
    from mrec import gui

    return gui.main()


if __name__ == "__main__":
    sys.exit(main())
