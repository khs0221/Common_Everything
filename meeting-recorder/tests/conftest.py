"""시험 공통 설정.

모델이 없거나(models/ 폴더) 화면을 띄울 수 없으면 해당 시험은 skip 이 아니라
'실행 안 함(deselected)'으로 빼고, 끝에 이유와 개수를 따로 적습니다.
모델은 `python tools/download_models.py` 로 받습니다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mrec import models  # noqa: E402
from mrec.paths import models_dir  # noqa: E402

_NOT_RUN: dict[str, list[str]] = {}
_TK: dict = {"root": None}


def _gui_available() -> str | None:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return "tkinter 없음"
    if os.name != "nt" and not os.environ.get("DISPLAY"):
        return "디스플레이 없음(DISPLAY)"
    return None


def pytest_collection_modifyitems(config, items):
    missing = models.missing_models(models_dir())
    gui_reason = _gui_available()
    keep, drop = [], []
    if gui_reason is None and any(it.get_closest_marker("gui") for it in items):
        # 화면 시험이 있으면 Tk 루트를 세션에서 한 번만 만듭니다(재시도 포함).
        from _tkhelp import ATTEMPTS, make_tk_root

        root, err = make_tk_root()
        if root is None:
            first = str(err).splitlines()[0] if str(err) else type(err).__name__
            gui_reason = f"Tk 초기화 실패({ATTEMPTS}번 시도): {first}"
        _TK["root"] = root
    for it in items:
        if it.get_closest_marker("models") and missing:
            _NOT_RUN.setdefault(f"모델 없음({models_dir()})", []).append(it.nodeid)
            drop.append(it)
        elif it.get_closest_marker("gui") and gui_reason:
            _NOT_RUN.setdefault(gui_reason, []).append(it.nodeid)
            drop.append(it)
        else:
            keep.append(it)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def pytest_terminal_summary(terminalreporter):
    if not _NOT_RUN:
        return
    tr = terminalreporter
    tr.section("실행하지 않은 시험(skip 아님)")
    for reason, ids in _NOT_RUN.items():
        tr.write_line(f"{reason}: {len(ids)}개")
        for i in ids:
            tr.write_line(f"  - {i}")


def pytest_sessionfinish(session, exitstatus):
    root = _TK.get("root")
    if root is not None:
        try:
            root.destroy()
        except Exception:
            pass
        _TK["root"] = None


@pytest.fixture
def tk_window():
    """세션 공유 Tk 루트 아래의 새 창(Toplevel). 시험이 끝나면 예약을 모두 취소하고 닫습니다."""
    import tkinter as tk

    from _tkhelp import cancel_pending_after

    root = _TK["root"]
    assert root is not None, "화면 시험은 conftest 가 만든 Tk 루트가 있어야 합니다"
    win = tk.Toplevel(root)
    try:
        yield win
    finally:
        cancel_pending_after(root)
        try:
            win.destroy()
        except tk.TclError:
            pass
        root.update()


@pytest.fixture
def samples() -> Path:
    return ROOT / "samples"
