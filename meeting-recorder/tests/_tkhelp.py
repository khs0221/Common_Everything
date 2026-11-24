"""화면 시험용 Tk 루트 만들기(재시도 포함).

제품은 한 프로세스에서 Tk 루트를 한 번만 만듭니다(`mrec.gui.main`). 시험도 같게 세션에서 한 번만
만들어 공유하고, 각 시험은 그 아래 Toplevel 에 화면을 올립니다. 윈도우 CI 러너에서 Tk 루트를
여러 번 만들 때 Tcl 초기화가 간헐적으로 실패한 일이 있어(tk.tcl 이 icons.tcl 을 못 읽음),
Tk() 생성 단계의 TclError 에 한해 짧게 다시 시도합니다.
"""
from __future__ import annotations

import time

ATTEMPTS = 3
WAIT_S = 0.5


def make_tk_root(tk_module=None, attempts: int = ATTEMPTS, wait_s: float = WAIT_S):
    """(root, None) 또는 실패 시 (None, 마지막 TclError). Tk() 생성 외의 오류는 그대로 던집니다."""
    if tk_module is None:
        import tkinter as tk_module
    last = None
    for i in range(attempts):
        try:
            root = tk_module.Tk()
        except tk_module.TclError as e:
            last = e
            if i < attempts - 1:
                time.sleep(wait_s)
            continue
        root.withdraw()
        return root, None
    return None, last


def cancel_pending_after(root) -> None:
    """시험이 끝난 화면의 after 예약(주기 갱신 등)을 모두 취소합니다(다음 시험에 새지 않게)."""
    for aid in root.tk.splitlist(root.tk.call("after", "info")):
        try:
            root.after_cancel(aid)
        except Exception:
            pass
