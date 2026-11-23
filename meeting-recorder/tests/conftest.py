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


@pytest.fixture
def samples() -> Path:
    return ROOT / "samples"
