import threading

import numpy as np
import pytest

from mrec.devices import FakeBackend, FakeSource, _run_in_mta, tone


def test_run_in_mta_uses_other_thread_and_propagates():
    main = threading.get_ident()
    assert _run_in_mta(lambda: threading.get_ident()) != main
    assert _run_in_mta(lambda a, b: a + b, 2, 3) == 5
    with pytest.raises(ValueError):
        _run_in_mta(lambda: (_ for _ in ()).throw(ValueError("x")))


def test_fake_source_realtime_blocks_and_gap():
    src = FakeSource(16000, tone(440, 0.1, 16000), gaps=[(0.0, 0.3)])
    import time

    t = time.monotonic()
    blk = src.read()
    waited = time.monotonic() - t
    assert len(blk) == 1600 and blk.dtype == np.float32
    assert waited >= 0.35  # 앞 0.3초는 데이터가 오지 않음
    src.close()


def test_fake_backend_lists_defaults():
    b = FakeBackend()
    assert b.list_microphones()[0].is_default and b.list_speakers()[0].is_default
