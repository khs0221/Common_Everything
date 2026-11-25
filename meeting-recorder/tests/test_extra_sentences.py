"""자체 시험의 '정답에 없는 문장 덧붙음' 검사(감사 R6 I2). 모델 없이 R6 에서 실제로 나온 출력으로."""
import json
from pathlib import Path

from mrec.merge import Utterance
from mrec.selftest import extra_sentences

TRUTH = json.loads((Path(__file__).resolve().parent.parent / "samples" / "sample_mixed_lang_truth.json")
                   .read_text(encoding="utf-8"))


def _u(text, start=55.4):
    return Utterance(start, start + 6, "me", text)


def test_appended_thanks_is_caught_but_recognition_errors_are_not():
    # R6 int8 저장본이 실제로 낸 마지막 줄(감사합니다. 는 말하지 않음)
    bad = _u("필리핀 정복에 사용된 미국 제국주의의 전쟁 비용은 필리핀이 자신들에 이여 제급되었다. 감사합니다.")
    assert extra_sentences(TRUTH, [bad]) == ["감사합니다."]
    # 원본(fp16)이 낸 줄: 인식 오류는 있지만 덧붙은 문장은 없음
    ok = _u("필리핀 정복에 사용된 미국 제국주의의 전쟁 비용은 필리핀이 자신들에게 이외에 제급되었다.")
    assert extra_sentences(TRUTH, [ok]) == []


def test_normal_outputs_have_no_extra_sentences():
    utts = [_u("스페인 사람들이 3세기 동안 지속된 시민지 시대를 시작했다.", 7.3),
            _u("바다학원은 바다에 살지 않으며 록햄프턴 북쪽 강어기에 주로 서식한다.", 38.4),
            _u("The first cases of the disease this season were reported in late July. The major religion in "
               "Moldova is Orthodox Christian. Good morning, everyone. Thanks for joining the weekly Project Call.", 44)]
    assert extra_sentences(TRUTH, utts) == []


def test_typical_hallucinations_are_caught():
    for h in ("Thank you.", "다음 영상에서 만나요.", "So, let's go.", "시청해 주셔서 감사합니다."):
        assert extra_sentences(TRUTH, [_u(h)]) == [h], h
