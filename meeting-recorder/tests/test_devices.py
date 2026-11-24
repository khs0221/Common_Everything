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


def test_fake_source_stall_and_zero_modes():
    import time

    src = FakeSource(16000, tone(440, 0.1, 16000), gaps=[(0.0, 0.3)], gap_mode="stall")
    t = time.monotonic()
    blk = src.read()
    assert len(blk) == 1600 and blk.dtype == np.float32
    assert time.monotonic() - t >= 0.35  # 멈춤: 앞 0.3초는 데이터가 오지 않음
    src.close()
    src = FakeSource(16000, tone(440, 0.1, 16000), gaps=[(0.0, 0.3)])  # 기본: soundcard 처럼 0
    blk = src.read()
    assert len(blk) == 1600 and np.all(blk == 0)
    src.cancel()
    assert len(src.read()) == 0  # 취소되면 바로 빈 조각


def test_fake_backend_lists_defaults():
    b = FakeBackend()
    assert b.list_microphones()[0].is_default and b.list_speakers()[0].is_default


def test_tk_root_retry_only_on_tcl_error():
    """화면 시험용 Tk 루트: Tk() 의 TclError 만 다시 시도하고, 계속 실패하면 오류를 돌려줍니다."""
    import types

    from _tkhelp import make_tk_root

    class TclError(Exception):
        pass

    class Root:
        withdrawn = False

        def withdraw(self):
            self.withdrawn = True

    def fake(fail_times, exc=TclError):
        n = {"calls": 0}

        def Tk():
            n["calls"] += 1
            if n["calls"] <= fail_times:
                raise exc("Can't find a usable tk.tcl")
            return Root()

        return types.SimpleNamespace(Tk=Tk, TclError=TclError), n

    mod, n = fake(2)
    root, err = make_tk_root(mod, attempts=3, wait_s=0)
    assert root is not None and root.withdrawn and err is None and n["calls"] == 3
    mod, n = fake(5)
    root, err = make_tk_root(mod, attempts=3, wait_s=0)
    assert root is None and isinstance(err, TclError) and n["calls"] == 3
    mod, _ = fake(1, exc=ValueError)  # TclError 가 아닌 오류는 감추지 않음
    with pytest.raises(ValueError):
        make_tk_root(mod, attempts=3, wait_s=0)
