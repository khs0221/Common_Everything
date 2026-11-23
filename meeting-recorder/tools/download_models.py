"""빌드용: 동봉할 모델을 내려받아 해시를 확인하고 models/ 폴더에 넣습니다.

프로그램(exe)은 실행 중 아무것도 내려받지 않습니다. 이 스크립트는 빌드(GitHub Actions)와
개발 PC 에서만 씁니다.

사용: python tools/download_models.py [대상 폴더(기본: models)]
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mrec.models import FILES  # noqa: E402


def fetch(url: str, dst: Path, tries: int = 4) -> None:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "meeting-recorder-build"})
            with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            return
        except Exception as e:  # 네트워크 일시 오류는 다시 시도
            if i == tries - 1:
                raise
            wait = 2 ** (i + 1)
            print(f"  다시 시도 {i + 1}/{tries - 1} ({e}); {wait}초 뒤")
            time.sleep(wait)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def main(target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    cache: dict[str, Path] = {}
    tmp = Path(tempfile.mkdtemp(prefix="mrec_dl_"))
    bad = []
    try:
        for f in FILES:
            out = target / f.rel
            if out.is_file() and (not f.sha256 or sha256(out) == f.sha256):
                print(f"있음: {f.rel}")
                continue
            if f.url not in cache:
                print(f"받는 중: {f.url}")
                p = tmp / f"dl_{len(cache)}"
                fetch(f.url, p)
                cache[f.url] = p
            src = cache[f.url]
            out.parent.mkdir(parents=True, exist_ok=True)
            if f.archive_member:
                with tarfile.open(src, "r:*") as tf:
                    member = tf.getmember(f.archive_member)
                    if not member.isfile():
                        raise RuntimeError(f"압축 안 항목이 파일이 아닙니다: {f.archive_member}")
                    with tf.extractfile(member) as r, open(out, "wb") as w:
                        shutil.copyfileobj(r, w)
            else:
                shutil.copyfile(src, out)
            if f.sha256 and sha256(out) != f.sha256:
                bad.append(f.rel)
                print(f"해시 불일치: {f.rel}")
            else:
                print(f"완료: {f.rel} ({out.stat().st_size / 1e6:.1f}MB)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if bad:
        print("해시가 다른 파일:", bad)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "models"))
