"""selftest 의 '화면 경로 장치 검사'(R2 재현용)가 실패·성공을 제대로 적는지."""
from mrec import devices, selftest


def _by_name(results):
    return {r["name"]: r for r in results}


def test_backend_open_failure_is_recorded_as_failure_with_detail(monkeypatch):
    def boom():
        raise RuntimeError("Error 0x100000001")

    monkeypatch.setattr(devices, "default_backend", boom)
    results = []
    selftest.gui_device_path(results)
    r = _by_name(results)["화면 경로: 장치 계층 열기(default_backend)"]
    assert r["ok"] is False
    assert "RuntimeError: Error 0x100000001" in r["detail"] and "boom" in r["detail"]


def test_fake_backend_path_succeeds(monkeypatch):
    monkeypatch.setattr(devices, "default_backend", lambda: devices.FakeBackend())
    results = []
    selftest.gui_device_path(results)
    r = _by_name(results)
    assert r["화면 경로: 장치 계층 열기(default_backend)"]["ok"]
    assert r["화면 경로: 마이크 목록 읽기"]["ok"] and r["화면 경로: 스피커 목록 읽기"]["ok"]
    assert r["화면 경로: 기본 마이크 id(녹음 스레드 방식)"]["ok"]
    assert r["화면 경로: 기본 스피커 id(녹음 스레드 방식)"]["ok"]


def test_zero_devices_is_reference_only(monkeypatch):
    class Empty(devices.FakeBackend):
        def list_microphones(self):
            return []

        def default_microphone_id(self):
            raise RuntimeError("Error 0x80070490")

    monkeypatch.setattr(devices, "default_backend", lambda: Empty())
    results = []
    selftest.gui_device_path(results)
    r = _by_name(results)
    assert r["화면 경로: 마이크 목록 읽기"]["ok"] and "0개" in r["화면 경로: 마이크 목록 읽기"]["detail"]
    assert r["화면 경로: 기본 마이크 id(참고: 장치 0개)"]["ok"]


def test_real_device_checks_with_fake_backend():
    from mrec.devices import FakeBackend

    results = []
    selftest.real_device_checks(FakeBackend(), results)
    r = _by_name(results)
    assert r["실제 장치 열기: 마이크 시험용 마이크"]["ok"] and "fake" in r["실제 장치 열기: 마이크 시험용 마이크"]["detail"]
    assert r["실제 장치 열기: 기본 스피커 루프백"]["ok"]


def test_real_device_checks_reports_open_failure_and_zero_devices():
    from mrec.devices import DeviceOpenError, FakeBackend

    class Bad(FakeBackend):
        def open_microphone(self, device_id):
            raise DeviceOpenError("m", [{"combo": "c", "type": "AssertionError", "repr": "AssertionError()",
                                         "hresult": None, "where": "f.py:1 g"}], {})

    results = []
    selftest.real_device_checks(Bad(), results)
    r = _by_name(results)["실제 장치 열기: 마이크 시험용 마이크"]
    assert r["ok"] is False and "AssertionError()" in r["detail"]

    class Empty(FakeBackend):
        def list_microphones(self):
            return []

        def default_speaker_id(self):
            return None

    results = []
    selftest.real_device_checks(Empty(), results)
    assert results == [{"name": "실제 장치 열기(참고: 마이크 0개)", "ok": True, "detail": "CI 등 장치 없는 환경"}]
