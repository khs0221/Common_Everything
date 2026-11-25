"""빌드용 모델 받기의 멈춤 감시(R6). 네트워크 없이 가짜 응답으로 시험합니다."""
import importlib.util
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("dl", ROOT / "tools" / "download_models.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Slow:
    def __init__(self, chunks, clock, step):
        self.chunks, self.clock, self.step = list(chunks), clock, step

    def read(self, n):
        self.clock[0] += self.step
        return self.chunks.pop(0) if self.chunks else b""


def test_fast_download_completes(monkeypatch):
    dl = _load()
    clock = [0.0]
    monkeypatch.setattr(dl.time, "monotonic", lambda: clock[0])
    out = io.BytesIO()
    dl._copy_with_watchdog(Slow([b"x" * (1 << 20)] * 50, clock, 0.1), out)
    assert out.tell() == 50 << 20


def test_stalled_download_raises(monkeypatch):
    dl = _load()
    clock = [0.0]
    monkeypatch.setattr(dl.time, "monotonic", lambda: clock[0])
    with pytest.raises(dl.SlowDownload):
        dl._copy_with_watchdog(Slow([b"x" * 1024] * 1000, clock, 1.0), io.BytesIO())  # 1KB/초
