"""turbo int8 캐시 이름이 줄 끝(LF/CRLF)과 무관한지(R6 수정 1회차, 윈도우 빌드 캐시 불일치)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ci_cache_key  # noqa: E402


def _copy(tmp_path, crlf: bool) -> Path:
    for rel in ci_cache_key.KEY_FILES:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        data = (ROOT / rel).read_bytes().replace(b"\r\n", b"\n")
        dst.write_bytes(data.replace(b"\n", b"\r\n") if crlf else data)
    return tmp_path


def test_same_key_for_lf_and_crlf(tmp_path):
    lf = ci_cache_key.cache_key(_copy(tmp_path / "lf", False))
    crlf = ci_cache_key.cache_key(_copy(tmp_path / "crlf", True))
    assert lf == crlf == ci_cache_key.cache_key()
    assert lf.startswith("mrec-turbo-int8-") and len(lf) == len("mrec-turbo-int8-") + 32


def test_key_changes_when_content_changes(tmp_path):
    d = _copy(tmp_path / "a", False)
    k1 = ci_cache_key.cache_key(d)
    p = d / "requirements-convert.txt"
    p.write_bytes(p.read_bytes() + b"# x\n")
    assert ci_cache_key.cache_key(d) != k1


def test_cli_prints_and_appends_github_output(tmp_path):
    gh = tmp_path / "gh_output"
    gh.write_bytes(b"before=1\n")  # write_text 는 윈도우에서 \r\n 으로 저장되므로 바이트로(수정 1회차 CI)
    env = {**__import__("os").environ, "GITHUB_OUTPUT": str(gh)}
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "ci_cache_key.py")],
                         capture_output=True, text=True, check=True, env=env).stdout.strip()
    assert out == f"key={ci_cache_key.cache_key()}"
    # 도구가 덧붙이는 줄은 운영체제와 무관하게 LF 하나로 끝남(러너는 LF·CRLF 모두 읽지만 값에 \r 이 섞이지 않게)
    assert gh.read_bytes() == f"before=1\nkey={ci_cache_key.cache_key()}\n".encode()
    assert b"\r" not in gh.read_bytes()


def test_workflow_uses_key_tool_not_hashfiles():
    wf = (ROOT.parent / ".github" / "workflows" / "meeting-recorder-build.yml").read_text(encoding="utf-8")
    assert "tools/ci_cache_key.py" in wf
    assert "mrec-turbo-int8-${{ hashFiles" not in wf
