"""빌드 워크플로의 turbo int8 캐시 이름(키)을 만듭니다(R6 수정 1회차).

리눅스 변환 작업과 윈도우 빌드 작업이 같은 이름을 써야 캐시를 넘겨받을 수 있습니다. 깃허브의 hashFiles 는
파일 내용을 그대로 해시하므로, 윈도우에서 받은 파일의 줄 끝(CRLF)이 리눅스(LF)와 달라 이름이 늘 어긋났습니다.
여기서는 줄 끝을 LF 로 맞춘 뒤 해시하므로 운영체제와 무관하게 같은 이름이 나옵니다.

사용: python tools/ci_cache_key.py  →  "key=mrec-turbo-int8-<해시 앞 32자>" 한 줄을 출력하고,
환경 변수 GITHUB_OUTPUT 이 있으면 그 파일에도 덧붙입니다(쉘마다 다른 리디렉션 인코딩을 피함).
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 변환 결과를 정하는 파일: 원본·결과 해시(models.py), 변환 절차, 변환 도구 판 고정
KEY_FILES = ("mrec/models.py", "tools/convert_turbo_int8.py", "requirements-convert.txt")
PREFIX = "mrec-turbo-int8-"


def cache_key(root: Path = ROOT) -> str:
    h = hashlib.sha256()
    for rel in KEY_FILES:
        data = (root / rel).read_bytes().replace(b"\r\n", b"\n")
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
    return PREFIX + h.hexdigest()[:32]


def main() -> int:
    line = f"key={cache_key()}"
    print(line)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
