"""실행 중 인터넷 접속을 막습니다.

프로그램 시작 때 `enable()` 를 한 번 부르면, 이 프로세스의 파이썬 코드가
바깥(127.0.0.1 등 자기 PC 가 아닌 곳)으로 연결하려 할 때 예외가 납니다.
모델은 모두 프로그램 폴더에 들어 있으므로 연결할 일이 없습니다.
"""
from __future__ import annotations

import os
import socket

_ENABLED = False
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


class NetworkBlockedError(RuntimeError):
    pass


def _host_of(address) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


def _check(address) -> None:
    # AF_UNIX 등 파일 경로 소켓은 통과(바깥 연결이 아님)
    if isinstance(address, (str, bytes)):
        return
    host = _host_of(address)
    if host not in _LOCAL_HOSTS:
        raise NetworkBlockedError(f"외부 접속 차단: {host}")


def enable() -> None:
    """외부 접속 차단을 켭니다(여러 번 불러도 됩니다)."""
    global _ENABLED
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    if _ENABLED:
        return
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(address)
        return orig_connect(self, address)

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _check(address)
        return orig_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and str(host) not in _LOCAL_HOSTS:
            if isinstance(host, bytes):
                host = host.decode("ascii", "replace")
            raise NetworkBlockedError(f"외부 이름 조회 차단: {host}")
        return orig_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
    _ENABLED = True


def is_enabled() -> bool:
    return _ENABLED
