"""용어 파일 읽기·힌트 만들기·Copilot 교정 목록(R4). 용어 파일 내용은 시험용 임시 파일만 씁니다."""
from mrec import glossary, output
from mrec.merge import ME, Utterance


def _write(p, text, enc="utf-8"):
    p.write_bytes(text.encode(enc))


def test_read_terms_comments_blank_dedupe_bom(tmp_path):
    p = tmp_path / "t.txt"
    _write(p, "﻿# 주석\nAlpha\n\n  beta   term \nALPHA\n#x\nGamma 1.2\n")
    assert glossary.read_terms(p) == ["Alpha", "beta term", "Gamma 1.2"]


def test_read_terms_cp949_and_missing(tmp_path):
    p = tmp_path / "t.txt"
    _write(p, "# 한글 주석\nDelta\n", enc="cp949")
    assert glossary.read_terms(p) == ["Delta"]
    assert glossary.read_terms(tmp_path / "none.txt") == []


def test_build_hotwords_keeps_whole_terms_within_limit():
    count = lambda s: len(s.split())  # noqa: E731  가짜 토큰 수: 단어 수
    hot, used, dropped = glossary.build_hotwords(["a b", "c", "d e f", "g"], max_tokens=4, count=count)
    assert hot == "a b, c" and used == 2 and dropped == 2


def test_load_hints_missing_or_empty_dir(tmp_path):
    assert glossary.load_hints(tmp_path) == {}
    (tmp_path / glossary.HINT_FILE).write_text("# 주석만\n\n", encoding="utf-8")
    assert glossary.load_hints(tmp_path) == {}
    assert glossary.load_full(tmp_path) == []
    assert "용어 파일 없음" in glossary.status_text(tmp_path)


def test_load_hints_with_real_tokenizer(tmp_path):
    (tmp_path / glossary.HINT_FILE).write_text("Term1\nTerm 2\n", encoding="utf-8")
    h = glossary.load_hints(tmp_path)
    assert h["hotwords"] == "Term1, Term 2" and h["used"] == 2 and h["dropped"] == 0


def test_long_hint_list_is_cut_at_token_limit(tmp_path):
    terms = [f"Word{i} Term{i}" for i in range(200)]
    (tmp_path / glossary.HINT_FILE).write_text("\n".join(terms), encoding="utf-8")
    h = glossary.load_hints(tmp_path)
    assert 0 < h["used"] < 200 and h["used"] + h["dropped"] == 200
    assert glossary._count_tokens(" " + h["hotwords"]) <= glossary.HOT_MAX_TOKENS
    assert h["hotwords"].startswith("Word0 Term0, Word1 Term1")  # 앞에서부터(중요한 순서)


def test_copilot_text_includes_glossary_only_when_present(tmp_path, monkeypatch):
    utts = [Utterance(0, 1, ME, "hello", "en")]
    meta = {"seconds": 1}
    monkeypatch.setenv("MREC_GLOSSARY_DIR", str(tmp_path))
    outs = output.write_outputs(tmp_path, utts, meta)
    txt = outs["copilot"].read_text(encoding="utf-8-sig")
    assert "=== 용어 목록 시작 ===" not in txt and "[00:00:00] 나: hello" in txt
    assert "교정된 대화록" in txt and "원래 표기를 괄호로" in txt and "언어를 바꾸거나 번역하지" in txt
    (tmp_path / glossary.FULL_FILE).write_text("# 분류\nTermA\nTermB\n", encoding="utf-8")
    outs = output.write_outputs(tmp_path, utts, meta)
    txt = outs["copilot"].read_text(encoding="utf-8-sig")
    assert "=== 용어 목록 시작 ===\nTermA\nTermB\n=== 용어 목록 끝 ===" in txt
    assert txt.index("용어 목록 끝") < txt.index("=== 회의 기록 시작 ===")
    md = outs["record"].read_text(encoding="utf-8-sig")
    assert "TermA" not in md  # 기록.md 는 그대로


def test_repo_glossary_files_follow_format():
    """저장소의 용어 파일(내용은 조사 담당 작성)이 형식대로 읽히는지만 확인합니다."""
    d = glossary.glossary_dir()
    hint, full = glossary.read_terms(d / glossary.HINT_FILE), glossary.read_terms(d / glossary.FULL_FILE)
    if not hint and not full:
        return  # 파일이 없어도 프로그램은 동작해야 하므로 실패로 보지 않음
    assert len(hint) <= 80 and len(full) <= 300


def test_transcript_and_guide_files_and_rename(tmp_path, monkeypatch):
    """대화록.txt(지시문 없음)·교정지침.md(규칙+용어, 프로그램 폴더 원본도 갱신)·이름 바꾸기 반영(R4)."""
    gdir = tmp_path / "glossary"
    gdir.mkdir()
    (gdir / glossary.FULL_FILE).write_text("# 분류\nTermA\n", encoding="utf-8")
    monkeypatch.setenv("MREC_GLOSSARY_DIR", str(gdir))
    meet = tmp_path / "meet"
    meet.mkdir()
    utts = [Utterance(0, 1, ME, "hello there", "en"), Utterance(2, 3, "spk1", "안녕하세요", "ko")]
    output.write_outputs(meet, utts, {"seconds": 3, "language": "auto"})
    tr = (meet / output.TRANSCRIPT_TXT).read_text(encoding="utf-8-sig")
    assert "규칙" not in tr and "[00:00:00] 나: hello there" in tr and "[00:00:02] 참석자 1: 안녕하세요" in tr
    assert "회의 길이: 00:00:03" in tr and "발언자: 나, 참석자 1" in tr
    guide = (meet / output.GUIDE_MD).read_text(encoding="utf-8")
    assert "- TermA" in guide and "변경 목록" in guide and "번역하지" in guide
    assert (gdir / output.GUIDE_MD).read_text(encoding="utf-8") == guide  # 프로그램 폴더 원본도 같음
    cop = (meet / output.COPILOT_TXT).read_text(encoding="utf-8-sig")
    for rule in output.CORRECTION_RULES:  # 같은 규칙 문장을 씀
        assert rule in cop and rule in guide
    # 용어 파일을 고치면 다음 처리 때 지침이 다시 만들어짐
    (gdir / glossary.FULL_FILE).write_text("TermB\n", encoding="utf-8")
    output.apply_names(meet, {"spk1": "Alice"})
    assert "- TermB" in (gdir / output.GUIDE_MD).read_text(encoding="utf-8")
    tr2 = (meet / output.TRANSCRIPT_TXT).read_text(encoding="utf-8-sig")
    assert "[00:00:02] Alice: 안녕하세요" in tr2
    md = (meet / output.RECORD_MD).read_text(encoding="utf-8-sig")
    assert "TermB" not in md and "규칙" not in md


def test_lenient_reading_of_tool_made_files(tmp_path):
    p = tmp_path / "t.txt"
    p.write_text(
        "# 제목\n"
        "- Cpk\n"
        "* Ppk; Cp, Pp\n"
        "• Gage R&R\n"
        "1. UN 38.3\n"
        "2) X-bar R chart\n"
        "\"poka-yoke\"\n"
        "IEC 62660 - cell performance test standard\n"
        "DCIR: direct current internal resistance\n"
        "cell/module/pack\n"
        "1,000 cycles\n"
        "CPK\n"
        + "This line is a long sentence that is clearly not a single glossary term at all.\n",
        encoding="utf-8")
    terms, st = glossary.read_terms_stats(p)
    assert terms == ["Cpk", "Ppk", "Cp", "Pp", "Gage R&R", "UN 38.3", "X-bar R chart", "poka-yoke",
                     "IEC 62660", "DCIR", "cell/module/pack", "1,000 cycles"]
    assert st["skipped_long"] == 1 and st["duplicates"] == 1


def test_status_shows_read_and_used_counts(tmp_path):
    (tmp_path / glossary.HINT_FILE).write_text("A1\nB2\n" + "x" * 80 + "\n", encoding="utf-8")
    t = glossary.status_text(tmp_path)
    assert "2개 읽음 중 2개 사용" in t and "너무 긴 줄 1개" in t


def test_utf16_files_are_read(tmp_path):
    for enc, bom in (("utf-16", True), ("utf-16-le", False), ("utf-16-be", False)):
        p = tmp_path / f"t_{enc}.txt"
        p.write_bytes("# 주석\nCpk\nUN 38.3\n".encode(enc))
        assert glossary.read_terms(p) == ["Cpk", "UN 38.3"], enc


def test_unreadable_file_is_reported(tmp_path):
    (tmp_path / glossary.HINT_FILE).write_bytes(b"\x81\xff\xfe\x80\x80abc\x9f\xff")
    terms, st = glossary.read_terms_stats(tmp_path / glossary.HINT_FILE)
    assert terms == [] and st["unreadable"]
    assert "hint_terms.txt 읽지 못함" in glossary.status_text(tmp_path)


def test_missing_for_allows_other_quality(tmp_path):
    from mrec import models

    for f in models.FILES:
        if not f.rel.startswith(models.WHISPER_TURBO_DIR):
            p = tmp_path / f.rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
    assert models.missing_for("base", False, tmp_path) == []           # 판별 모델은 있음
    assert models.missing_for("turbo", True, tmp_path)                 # 옮기기 모델이 없으면 막힘
    assert all(m.startswith(models.WHISPER_TURBO_DIR) for m in models.missing_for("turbo", True, tmp_path))


def test_hint_echo_catches_list_without_commas_and_short_insertions():
    """R6(감사 R4 N1): 쉼표 없는 용어 나열과 1~2단어 끼어듦."""
    from types import SimpleNamespace

    from mrec.transcribe import Transcriber

    t = Transcriber.__new__(Transcriber)
    t.hotwords = "Cpk, Ppk, Cp, Pp, Gage R&R, MSA, SPC, FMEA, DFMEA"
    t.hint_vocab_text = None
    assert t.hint_echo("Cpk Ppk Cp Pp MSA")                      # 쉼표 없는 나열
    assert not t.hint_echo("We checked the Cpk and Ppk values today")  # 실제 문장
    seg = lambda text, nsp, lp: SimpleNamespace(text=text, no_speech_prob=nsp, avg_logprob=lp)  # noqa: E731
    assert t.short_hint_only(seg(" Cpk.", 0.6, -0.5))             # 말소리일 가능성 낮은 1단어
    assert t.short_hint_only(seg(" MSA SPC", 0.1, -1.2))          # 확신 낮은 2단어
    assert not t.short_hint_only(seg(" Cpk.", 0.05, -0.2))        # 또렷하게 말한 용어
    assert not t.short_hint_only(seg(" Thank you.", 0.9, -1.5))   # 용어가 아님(힌트와 무관)
