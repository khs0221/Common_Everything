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


def test_import_audio_module_retries_cleanly_and_without_com_init(tmp_path, monkeypatch):
    """soundcard 불러오기: COM 을 먼저 초기화하지 않은 새 스레드에서, 실패 뒤 다시 부르면 처음부터(R2)."""
    import sys

    from mrec import devices

    pkg = tmp_path / "fakesc_r2"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from fakesc_r2.mediafoundation import *\n", encoding="utf-8")
    (pkg / "mediafoundation.py").write_text(
        "import builtins\n"
        "builtins._fakesc_tries = getattr(builtins, '_fakesc_tries', 0) + 1\n"
        "if builtins._fakesc_tries == 1:\n"
        "    raise RuntimeError('Error 0x100000001')\n"
        "VALUE = 42\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    com_threads = []
    monkeypatch.setattr(devices, "com_init", lambda: com_threads.append(threading.get_ident()) or False)
    import builtins

    try:
        with pytest.raises(RuntimeError, match="0x100000001"):
            devices.import_audio_module("fakesc_r2")
        # 반쯤 불러온 모듈이 남지 않음
        assert "fakesc_r2" not in sys.modules and "fakesc_r2.mediafoundation" not in sys.modules
        mod = devices.import_audio_module("fakesc_r2")
        assert mod.VALUE == 42 and builtins._fakesc_tries == 2
        assert devices.import_audio_module("fakesc_r2") is mod  # 두 번째부터는 불러 둔 것
        assert com_threads == []  # 불러오는 스레드는 COM 을 먼저 초기화하지 않음
    finally:
        for k in [k for k in sys.modules if k.startswith("fakesc_r2")]:
            del sys.modules[k]
        if hasattr(builtins, "_fakesc_tries"):
            del builtins._fakesc_tries


def test_import_audio_module_runs_off_main_thread(tmp_path, monkeypatch):
    import sys

    from mrec import devices

    (tmp_path / "fakesc_thr.py").write_text(
        "import threading\nTHREAD = threading.get_ident()\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        mod = devices.import_audio_module("fakesc_thr")
        assert mod.THREAD != threading.get_ident()
    finally:
        sys.modules.pop("fakesc_thr", None)
