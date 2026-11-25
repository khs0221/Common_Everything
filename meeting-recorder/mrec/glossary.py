"""분야 용어 파일 읽기(R4).

- `glossary/hint_terms.txt`: 인식 힌트용 용어. whisper 의 hotwords 로 영어 묶음에 줍니다.
- `glossary/glossary_full.txt`: Copilot 교정용 전체 목록. `Copilot용.txt` 의 지시문 뒤에 붙입니다.

형식: UTF-8, 한 줄에 용어 하나, `#` 으로 시작하는 줄은 주석, 빈 줄 허용.
파일이 없거나 비어 있으면 힌트·목록 없이 예전처럼 동작합니다. 두 파일은 exe 폴더에 그대로 있어
사용자가 메모장으로 고칠 수 있습니다(파일 내용은 이 프로그램이 만들지 않습니다).
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from . import models
from .paths import app_dir

log = logging.getLogger("mrec")

HINT_FILE = "hint_terms.txt"
FULL_FILE = "glossary_full.txt"

# faster-whisper 1.2.1 은 hotwords 를 최대 223토큰(max_length//2-1)에서 자르고, 프롬프트가 길수록
# 한 30초 창에서 만들 수 있는 글자 수(448토큰 - 프롬프트)가 줄어듭니다. 용어 단위로 이만큼까지만 씁니다.
HOT_MAX_TOKENS = 120
# 참고(R4 측정): 힌트 앞에 회의 분야 설명 문장을 붙여 봤으나 용어 적중·오류율이 나아지지 않아(37/47 vs 38/47)
# 쓰지 않습니다. 이전 문장 이어받기(condition_on_previous_text)도 일관된 이득이 없어 끈 채로 둡니다.


def glossary_dir() -> Path:
    env = os.environ.get("MREC_GLOSSARY_DIR")
    return Path(env) if env else app_dir() / "glossary"


MAX_TERM_CHARS = 60  # 이보다 긴 항목은 용어가 아닌 문장으로 보고 건너뜀
_BULLET = re.compile(r"^(?:[-*•·▪◦‣○●]|\d{1,3}[.)]|\(\d{1,3}\))\s+")
# 쉼표: 숫자 사이(1,000)는 나누지 않음. 세미콜론·전각 쉼표·가운뎃점 나열도 나눔
_SPLIT = re.compile(r"[;；、]|，|(?<!\d),|,(?!\d)")
_DESC = re.compile(r"\t|\s[-–—]\s|\s?:\s|：")  # '용어 - 설명', '용어: 설명', 탭 뒤 설명
_QUOTES = "\"'“”‘’`「」『』"


class Unreadable(Exception):
    """파일은 있는데 문자 인코딩을 알 수 없음."""


def _decode(path: Path) -> str | None:
    """None = 파일 없음. 읽을 수 없는 인코딩이면 Unreadable."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    # UTF-16(메모장 '유니코드' 저장 등, 감사 R4 E1): BOM 이 있거나, BOM 없이 바이트 절반이 0 인 경우
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    elif len(raw) >= 4 and raw.count(b"\x00") >= len(raw) // 3:
        # 영문·숫자는 한쪽 바이트가 0: 홀수 자리에 0 이 많으면 리틀 엔디언, 짝수 자리면 빅 엔디언
        odd, even = raw[1::2].count(0), raw[0::2].count(0)
        try:
            return raw.decode("utf-16-le" if odd >= even else "utf-16-be")
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:  # 메모장(ANSI) 등 CP949 저장 대비
        text = raw.decode("cp949")
        log.warning("용어 파일이 UTF-8 이 아니어서 CP949 로 읽음: %s", path.name)
        return text
    except UnicodeDecodeError:
        log.warning("용어 파일을 읽을 수 없음(문자 인코딩): %s", path.name)
        raise Unreadable(path.name)


def read_terms_stats(path: Path) -> tuple[list[str], dict]:
    """용어 목록과 읽기 통계. 다른 도구가 만든 파일도 받아들이도록 관대하게 읽습니다.

    - 한 줄에 용어 하나가 기본. 쉼표·세미콜론으로 나열된 줄은 나눔(숫자 사이 쉼표는 유지).
    - 마침표·공백·& · / · - 는 용어의 일부로 둠(예: UN 38.3, Gage R&R, X-bar R chart).
    - 줄 앞 글머리표(-, *, •, 1. 등)·앞뒤 공백·따옴표 제거, '용어 - 설명' / '용어: 설명' 은 앞 용어만.
    - '#' 줄은 주석, 빈 줄 허용. UTF-8(BOM 포함)·CP949 를 읽음.
    - 대소문자 무시 중복 제거(처음 표기 유지). MAX_TERM_CHARS 자를 넘는 항목은 건너뛰고 셈.
    """
    stats = {"terms": 0, "skipped_long": 0, "duplicates": 0, "unreadable": False}
    try:
        text = _decode(path)
    except Unreadable:
        stats["unreadable"] = True
        return [], stats
    if text is None:
        return [], stats
    seen, out = set(), []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = _BULLET.sub("", line)
        line = _DESC.split(line, maxsplit=1)[0]
        for part in _SPLIT.split(line):
            term = " ".join(part.split()).strip(_QUOTES + " ")
            term = _BULLET.sub("", term).strip(_QUOTES + " ")
            if not term:
                continue
            if len(term) > MAX_TERM_CHARS:
                stats["skipped_long"] += 1
                continue
            key = term.casefold()
            if key in seen:
                stats["duplicates"] += 1
                continue
            seen.add(key)
            out.append(term)
    stats["terms"] = len(out)
    if stats["skipped_long"]:
        log.warning("용어 파일 %s: 너무 긴 줄 %d개를 건너뜀", path.name, stats["skipped_long"])
    return out, stats


def read_terms(path: Path) -> list[str]:
    """용어 목록(read_terms_stats 의 목록만)."""
    return read_terms_stats(path)[0]


_TOKENIZER = None


def _count_tokens(text: str) -> int:
    """whisper 토크나이저로 센 토큰 수(small 기준. large-v3-turbo 와는 거의 같아 개수 추정에 씀)."""
    global _TOKENIZER
    if _TOKENIZER is None:
        from tokenizers import Tokenizer

        _TOKENIZER = Tokenizer.from_file(str(models.path(models.WHISPER_DIR) / "tokenizer.json"))
    return len(_TOKENIZER.encode(text, add_special_tokens=False).ids)


def build_hotwords(terms: list[str], max_tokens: int = HOT_MAX_TOKENS, count=None) -> tuple[str, int, int]:
    """앞에서부터 용어 단위로 max_tokens 안에 들어가는 만큼 ', ' 로 잇습니다. (문자열, 쓴 수, 뺀 수)."""
    count = count or _count_tokens
    used: list[str] = []
    for term in terms:
        cand = ", ".join(used + [term])
        if count(" " + cand) > max_tokens:
            break
        used.append(term)
    return ", ".join(used), len(used), len(terms) - len(used)


def load_hints(directory: Path | None = None, max_tokens: int = HOT_MAX_TOKENS) -> dict:
    """{'hotwords': 문자열, 'used': n, 'dropped': m, 'file': 이름} 또는 파일이 없거나 비면 {}."""
    path = (directory or glossary_dir()) / HINT_FILE
    terms, st = read_terms_stats(path)
    if not terms:
        return {}
    try:
        hot, used, dropped = build_hotwords(terms, max_tokens)
    except Exception as e:  # 토크나이저 문제 등: 힌트 없이 진행
        log.warning("용어 힌트를 만들지 못함: %s", type(e).__name__)
        return {}
    if dropped:
        log.warning("용어 힌트 %d개 중 앞의 %d개만 사용(길이 한도 %d토큰)", used + dropped, used, max_tokens)
    else:
        log.info("용어 힌트 %d개 사용", used)
    return {"hotwords": hot, "used": used, "dropped": dropped, "file": HINT_FILE, "read": len(terms),
            "skipped_long": st["skipped_long"], "terms_text": ", ".join(terms[:used])}


def load_full(directory: Path | None = None) -> list[str]:
    return read_terms((directory or glossary_dir()) / FULL_FILE)


def status_text(directory: Path | None = None) -> str:
    """화면에 보여 줄 한 줄 안내."""
    d = directory or glossary_dir()
    h = load_hints(directory)
    _, hst = read_terms_stats(d / HINT_FILE)
    full, fst = read_terms_stats(d / FULL_FILE)
    parts = []
    if hst["unreadable"]:
        parts.append(f"{HINT_FILE} 읽지 못함(메모장에서 'UTF-8'로 다시 저장해 주십시오)")
    if fst["unreadable"]:
        parts.append(f"{FULL_FILE} 읽지 못함(메모장에서 'UTF-8'로 다시 저장해 주십시오)")
    if h:
        s = f"힌트 파일 {h['read']}개 읽음 중 {h['used']}개 사용"
        if h["dropped"]:
            s += f"(길이 한도로 뒤쪽 {h['dropped']}개 제외)"
        if h.get("skipped_long"):
            s += f", 너무 긴 줄 {h['skipped_long']}개 건너뜀"
        parts.append(s)
    if full:
        s = f"교정 용어 {len(full)}개"
        if fst["skipped_long"]:
            s += f"(너무 긴 줄 {fst['skipped_long']}개 건너뜀)"
        parts.append(s)
    return ", ".join(parts) if parts else "용어 파일 없음(용어 없이 처리)"
