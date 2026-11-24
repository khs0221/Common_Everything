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

TRANSCRIPT_TXT = "대화록.txt"
GUIDE_MD = "교정지침.md"

# 교정 규칙: 교정지침.md 와 Copilot용.txt 지시문이 같은 문장을 씁니다(한 곳에서 관리).
CORRECTION_RULES = [
    "음성 인식 결과이므로 발음이 비슷한 다른 단어로 잘못 적힌 곳이 있을 수 있습니다. 앞뒤 대화의 문맥과 "
    "'용어 목록'을 근거로 올바른 단어를 추정해 바로잡으세요.",
    "바로잡은 곳은 원래 표기를 괄호로 남기세요. 예: Cpk(원래: CPK의)",
    "근거가 약하면 고치지 말고 그 자리에 '(불확실)'이라고 표시하세요.",
    "시각·발언자 표시는 바꾸지 말고, 발언을 합치거나 빼지 마세요.",
    "언어를 바꾸거나 번역하지 마세요(영어 발언은 영어로, 한국어 발언은 한국어로 둡니다).",
    "말하지 않은 내용을 추가하지 마세요. 용어 목록에 있다고 해서 회의에서 말하지 않은 용어를 넣지 마세요.",
    "사람 이름·회사명·제품명·숫자는 근거가 분명할 때만 바로잡으세요.",
    "고친 곳은 '시각, 원래 표기, 바꾼 표기, 이유' 로 된 변경 목록으로 따로 출력하세요.",
]
LOWCONF_RULE = "단어 뒤의 (?) 표시는 음성 인식의 확신이 낮은 단어입니다. 문맥으로 먼저 검토하고, 교정본에서는 (?) 표시를 지우세요."

SUMMARY_ITEMS = [
    "회의 개요 (3줄 이내)",
    "발언자별 주요 발언 (발언자마다 핵심 3개 이내)",
    "결정 사항",
    "할 일 (담당자, 기한이 기록에 있으면 함께)",
    "미결 사항 (결론이 나지 않았거나 다음에 다시 논의할 것)",
]


def _rules_block(low_conf: bool) -> list[str]:
    rules = list(CORRECTION_RULES)
    if low_conf:
        rules.insert(3, LOWCONF_RULE)
    return [f"{i}. {r}" for i, r in enumerate(rules, 1)]


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


def transcript_lines(utts: list[Utterance], names: dict[str, str], marked: bool = False) -> list[str]:
    """대화록 본문: 줄마다 [시:분:초] 발언자: 내용. marked=True 면 확신 낮은 단어에 (?) 를 붙인 글을 씁니다."""
    out = []
    for u in utts:
        text = (getattr(u, "marked", "") if marked else "") or u.text
        text = text.replace("\n", " ").strip()
        out.append(f"[{fmt_time(u.start)}] {display_name(u.speaker, names)}: {text}")
    return out


def _header_lines(utts: list[Utterance], meta: dict, names: dict[str, str]) -> list[str]:
    lines = []
    if meta.get("seconds") is not None:
        lines.append(f"회의 길이: {fmt_time(meta['seconds'])}")
    lines.append(f"언어 설정: {LANG_NAMES.get(meta.get('language'), meta.get('language'))}")
    lines.append(f"발언자: {_participants_line(utts, names)}")
    return lines


def render_transcript_txt(utts: list[Utterance], meta: dict, names: dict[str, str], marked: bool = False) -> str:
    """대화록.txt: 지시문 없이 머리말 + 대화록(파일을 받아 처리하는 AI 도구에 그대로 넘기는 용도)."""
    lines = ["# 대화록(자동 음성 인식 결과)"] + _header_lines(utts, meta, names)
    if marked:
        lines.append("(?) 표시: 음성 인식의 확신이 낮은 단어")
    lines.append("")
    lines += transcript_lines(utts, names, marked)
    return "\n".join(lines) + "\n"


def render_guide_md(glossary_terms: list[str] | None, low_conf: bool = False) -> str:
    """교정지침.md: 교정 규칙 + 분야 용어 목록(glossary_full.txt 에서 만듦)."""
    lines = ["# 대화록 교정 지침", "",
             "이 지침은 회의 녹음 프로그램이 만든 `대화록.txt` 를 교정할 때 씁니다. "
             "이 파일은 처리할 때마다 `glossary/glossary_full.txt` 로 다시 만들어지므로 직접 고치지 말고 용어 파일을 고치십시오.",
             "", "## 규칙", ""]
    lines += _rules_block(low_conf)
    lines += ["", "## 산출물", "",
              "1. 교정본 파일: 대화록과 같은 형식(줄마다 `[시:분:초] 발언자: 내용`)",
              "2. 변경 목록: 시각, 원래 표기, 바꾼 표기, 이유",
              "3. 요약은 요청받았을 때만 만듭니다.", "", "## 용어 목록(표기 기준)", ""]
    lines += [f"- {t}" for t in (glossary_terms or [])] or ["(용어 목록 없음)"]
    return "\n".join(lines) + "\n"


def render_copilot_txt(utts: list[Utterance], meta: dict, names: dict[str, str],
                       glossary_terms: list[str] | None = None, marked: bool = False) -> str:
    """Copilot용.txt: 채팅 붙여넣기용(지시문 + 용어 목록 + 대화록). 규칙은 교정지침과 같습니다."""
    lines = ["아래는 회의 녹음을 프로그램이 자동으로 글로 옮긴 대화록입니다. 다음 규칙으로 먼저 대화록을 바로잡아 주세요.", ""]
    lines += _rules_block(marked)
    lines += ["", "그다음 다음을 출력해 주세요.",
              "A. 교정된 대화록 (시각·발언자 형식은 그대로)",
              "B. 변경 목록 (시각, 원래 표기, 바꾼 표기, 이유)",
              "C. 요약(한국어)"]
    lines += [f"   {i}. {x}" for i, x in enumerate(SUMMARY_ITEMS, 1)]
    lines += ["", "기록에 없는 내용은 지어내지 말고 '기록에 없음'이라고 적어 주세요."]
    if glossary_terms:
        lines += ["", "=== 용어 목록 시작 ==="] + glossary_terms + ["=== 용어 목록 끝 ==="]
    lines += ["", "=== 회의 기록 시작 ==="] + _header_lines(utts, meta, names) + [""]
    lines += transcript_lines(utts, names, marked)
    lines.append("=== 회의 기록 끝 ===")
    return "\n".join(lines) + "\n"


def write_outputs(folder: Path, utts: list[Utterance], meta: dict, names: dict[str, str] | None = None) -> dict[str, Path]:
    """기록.md(원문), 대화록.txt, Copilot용.txt, 교정지침.md 를 씁니다. 이름을 바꿀 때도 이 함수로 다시 씁니다."""
    names = {k: clean_name(v) for k, v in (names or {}).items() if v and clean_name(v)}
    write_result_json(folder, utts, meta, names)
    marked = bool(meta.get("low_conf_marks"))
    try:
        from . import glossary

        terms = glossary.load_full()
    except Exception:
        terms = []
    paths = {"record": folder / RECORD_MD, "transcript": folder / TRANSCRIPT_TXT,
             "copilot": folder / COPILOT_TXT, "guide": folder / GUIDE_MD}
    # 메모장에서 한글이 깨지지 않게 UTF-8(BOM 포함)으로 씁니다(교정지침.md 는 BOM 없이).
    paths["record"].write_text(render_record_md(utts, meta, names), encoding="utf-8-sig")
    paths["transcript"].write_text(render_transcript_txt(utts, meta, names, marked), encoding="utf-8-sig")
    paths["copilot"].write_text(render_copilot_txt(utts, meta, names, terms, marked), encoding="utf-8-sig")
    guide = render_guide_md(terms, marked)
    paths["guide"].write_text(guide, encoding="utf-8")
    try:  # 프로그램 폴더의 glossary/교정지침.md 도 최신 용어로 다시 만듦(쓸 수 없으면 건너뜀)
        from . import glossary

        gdir = glossary.glossary_dir()
        if gdir.is_dir():
            (gdir / GUIDE_MD).write_text(guide, encoding="utf-8")
    except OSError:
        pass
    return paths


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
