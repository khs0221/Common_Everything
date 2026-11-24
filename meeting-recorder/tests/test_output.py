from mrec import output
from mrec.merge import ME, Utterance


def _utts():
    return [Utterance(0.0, 2.0, ME, "Hello all."), Utterance(3.0, 5.0, "spk1", "Hi."),
            Utterance(3725.0, 3730.0, "spk2", "Bye\nnow.")]


def test_fmt_and_names():
    assert output.fmt_time(3725.9) == "01:02:05"
    assert output.display_name(ME) == "나"
    assert output.display_name("spk2") == "참석자 2"
    assert output.display_name("spk2", {"spk2": "Bob"}) == "Bob"
    assert output.clean_name("  **Bob**\n Lee ") == "Bob Lee"


def test_write_outputs_and_apply_names(tmp_path):
    meta = {"started_at": "2026-11-23T10:00:00", "seconds": 3730.0, "language": "auto"}
    outs = output.write_outputs(tmp_path, _utts(), meta)
    md = outs["record"].read_text(encoding="utf-8-sig")
    txt = outs["copilot"].read_text(encoding="utf-8-sig")
    assert outs["record"].read_bytes().startswith(b"\xef\xbb\xbf")  # 메모장 호환 BOM
    assert "**[00:00:00] 나:** Hello all." in md
    assert "[01:02:05] 참석자 2: Bye now." in txt  # 줄바꿈 제거
    assert "결정 사항" in txt and "할 일" in txt and "미결 사항" in txt and "발언자별 주요 발언" in txt
    assert txt.index("=== 회의 기록 시작 ===") > txt.index("미결 사항")
    output.apply_names(tmp_path, {"spk1": "Alice", ME: "김"})
    txt2 = (tmp_path / output.COPILOT_TXT).read_text(encoding="utf-8-sig")
    assert "[00:00:03] Alice: Hi." in txt2 and "[00:00:00] 김: Hello all." in txt2
    assert "발언자: 김, Alice, 참석자 2" in txt2
    # 이름을 비우면 원래 표시로 돌아감
    output.apply_names(tmp_path, {"spk1": ""})
    txt3 = (tmp_path / output.COPILOT_TXT).read_text(encoding="utf-8-sig")
    assert "참석자 1: Hi." in txt3 and "김: Hello all." in txt3


def test_empty_result_message(tmp_path):
    outs = output.write_outputs(tmp_path, [], {"seconds": 5})
    assert "알아들은 말이 없습니다" in outs["record"].read_text(encoding="utf-8-sig")
