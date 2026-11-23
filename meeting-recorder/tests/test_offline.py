import socket

import pytest

from mrec import offline


def test_blocks_external_connections():
    offline.enable()
    with pytest.raises(offline.NetworkBlockedError):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(offline.NetworkBlockedError):
        socket.create_connection(("example.com", 443), timeout=1)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(offline.NetworkBlockedError):
            s.connect(("93.184.216.34", 443))
        with pytest.raises(offline.NetworkBlockedError):
            s.connect_ex(("8.8.8.8", 53))
    finally:
        s.close()


def test_allows_local_loopback():
    offline.enable()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    c = socket.create_connection(("127.0.0.1", port), timeout=2)
    c.close()
    srv.close()


def test_sets_offline_env(monkeypatch):
    import os

    offline.enable()
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert offline.is_enabled()
