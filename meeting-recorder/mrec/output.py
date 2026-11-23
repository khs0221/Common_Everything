"""결과물 쓰기: 기록.md, Copilot용.txt, 처리결과.json(이름 다시 정할 때 쓰는 원자료)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .merge import ME, UNKNOWN, Utterance, speakers_in

RECORD_MD = "기록.md"
COPILOT_TXT = "Copilot용.txt"
RESULT_JSON = "처리결과.json"

LANG_NAMES = {None: "자동(영어/한국어)", "auto": "자동(영어/한국어)", "en": "영어", "ko": "한국어"}

COPILOT_PROMPT = """아래는 회의 녹음을 프로그램이 자동으로 글로 옮긴 기록입니다.
자동 변환이라 잘못 알아들은 단어가 있을 수 있고, 발언자 구분도 틀릴 수 있습니다.
기록을 바탕으로 다음 항목을 한국어로 정리해 주세요. 사람 이름·회사명·제품명·숫자는 기록에 적힌 그대로 두세요.

1. 회의 개요 (3줄 이내)
2. 발언자별 주요 발언 (발언자마다 핵심 3개 이내)
3. 결정 사항
4. 할 일 (담당자, 기한이 기록에 있으면 함께)
5. 미결 사항 (결론이 나지 않았거나 다음에 다시 논의할 것)

기록에 없는 내용은 지어내지 말고 '기록에 없음'이라고 적어 주세요.
"""


def fmt_time(seconds: float) -> str:
    s = int(max(0.0, seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def display_name(key: str, names: dict[str, str] | None = None) -> str:
    names = names or {}
    if names.get(key):
        return names[key].strip()
    if key == ME:
        return "나"
    if key == UNKNOWN:
        return "참석자(구분 못 함)"
    if key.startswith("spk") and key[3:].isdigit():
        return f"참석자 {int(key[3:])}"
    return key


def clean_name(name: str) -> str:
    """이름 입력값 정리(줄바꿈·마크다운 기호 제거, 길이 제한)."""
    name = " ".join(name.split())
    for ch in "*_`#[]<>|":
        name = name.replace(ch, "")
    return name[:40]


def write_result_json(folder: Path, utts: list[Utterance], meta: dict, names: dict[str, str] | None = None) -> Path:
    data = {"version": 1, "meta": meta, "names": names or {}, "utterances": [u.to_dict() for u in utts]}
    p = folder / RESULT_JSON
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return p


def load_result_json(folder: Path) -> tuple[list[Utterance], dict, dict[str, str]]:
    data = json.loads((folder / RESULT_JSON).read_text(encoding="utf-8"))
    utts = [Utterance(**u) for u in data["utterances"]]
    return utts, data.get("meta", {}), data.get("names", {})


def _participants_line(utts: list[Utterance], names: dict[str, str]) -> str:
    keys = ([ME] if any(u.speaker == ME for u in utts) else []) + speakers_in(utts)
    return ", ".join(display_name(k, names) for k in keys) or "(발언 없음)"


def render_record_md(utts: list[Utterance], meta: dict, names: dict[str, str]) -> str:
    lines = ["# 회의 기록", ""]
    if meta.get("started_at"):
        lines.append(f"- 녹음 시각: {meta['started_at']}")
    if meta.get("seconds") is not None:
        lines.append(f"- 길이: {fmt_time(meta['seconds'])}")
    lines.append(f"- 언어 설정: {LANG_NAMES.get(meta.get('language'), meta.get('language'))}")
    lines.append(f"- 발언자: {_participants_line(utts, names)}")
    lines.append("- 자동 변환 결과입니다. 단어·발언자 구분이 틀릴 수 있습니다.")
    lines.append("")
    lines.append("---")
    lines.append("")
    if not utts:
        lines.append("(알아들은 말이 없습니다. 녹음 파일의 소리 크기와 장치 선택을 확인해 주십시오.)")
    for u in utts:
        text = u.text.replace("\n", " ").strip()
        lines.append(f"**[{fmt_time(u.start)}] {display_name(u.speaker, names)}:** {text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_copilot_txt(utts: list[Utterance], meta: dict, names: dict[str, str]) -> str:
    lines = [COPILOT_PROMPT.rstrip(), "", "=== 회의 기록 시작 ==="]
    lines.append(f"발언자: {_participants_line(utts, names)}")
    if meta.get("seconds") is not None:
        lines.append(f"회의 길이: {fmt_time(meta['seconds'])}")
    lines.append("")
    for u in utts:
        text = u.text.replace("\n", " ").strip()
        lines.append(f"[{fmt_time(u.start)}] {display_name(u.speaker, names)}: {text}")
    lines.append("=== 회의 기록 끝 ===")
    return "\n".join(lines) + "\n"


def write_outputs(folder: Path, utts: list[Utterance], meta: dict, names: dict[str, str] | None = None) -> dict[str, Path]:
    names = {k: clean_name(v) for k, v in (names or {}).items() if v and clean_name(v)}
    write_result_json(folder, utts, meta, names)
    md = folder / RECORD_MD
    txt = folder / COPILOT_TXT
    # 메모장에서 한글이 깨지지 않게 UTF-8(BOM 포함)으로 씁니다.
    md.write_text(render_record_md(utts, meta, names), encoding="utf-8-sig")
    txt.write_text(render_copilot_txt(utts, meta, names), encoding="utf-8-sig")
    return {"record": md, "copilot": txt}


def apply_names(folder: Path, names: dict[str, str]) -> dict[str, Path]:
    """참석자 이름을 정해 결과물을 다시 씁니다."""
    utts, meta, old = load_result_json(folder)
    merged = dict(old)
    for k, v in names.items():
        v = clean_name(v or "")
        if v:
            merged[k] = v
        else:
            merged.pop(k, None)
    return write_outputs(folder, utts, meta, merged)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
