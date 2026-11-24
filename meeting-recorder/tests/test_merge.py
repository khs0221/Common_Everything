from mrec.diarize import Turn
from mrec.merge import (ME, UNKNOWN, Utterance, assign_loop_segments, join_utterances, merge_tracks,
                        mic_utterances, relabel_turns, representative, speakers_in)
from mrec.transcribe import Segment, Word


def words(*items):
    return [Word(a, b, t) for a, b, t in items]


def test_relabel_by_first_appearance():
    turns = [Turn(5, 6, 3), Turn(0, 1, 7), Turn(2, 3, 3)]
    assert [s for _, _, s in relabel_turns(turns)] == ["spk1", "spk2", "spk2"]


def test_speaker_change_without_punctuation_is_split_when_long():
    turns = [Turn(0.0, 2.0, 0), Turn(2.0, 5.0, 1)]
    seg = Segment(0.0, 5.0, "x", "en", words((0.1, 0.5, " we"), (0.6, 1.0, " should"), (1.1, 1.9, " start"),
                                            (2.1, 2.5, " yes"), (2.6, 3.0, " i"), (3.1, 3.6, " agree")))
    out = assign_loop_segments([seg], turns)
    assert [(u.speaker, u.text) for u in out] == [("spk1", "we should start"), ("spk2", "yes i agree")]


def test_trailing_word_of_previous_segment_joins_next_sentence():
    """whisper 구간이 '… March. I' / 'can prepare …' 로 잘려도 'I' 는 다음 사람 문장에 붙어야 합니다."""
    turns = [Turn(0.0, 3.0, 0), Turn(3.6, 7.0, 1)]
    s1 = Segment(0.0, 3.4, "x", "en", words((0.5, 1.0, " In"), (1.1, 2.8, " March."), (2.9, 3.4, " I")))
    s2 = Segment(3.6, 7.0, "y", "en", words((3.7, 4.0, " can"), (4.1, 4.8, " prepare"), (4.9, 5.5, " slides.")))
    out = assign_loop_segments([s1, s2], turns)
    assert [(u.speaker, u.text) for u in out] == [("spk1", "In March."), ("spk2", "I can prepare slides.")]


def test_speaker_change_inside_one_segment_is_split():
    turns = [Turn(0.0, 2.0, 0), Turn(2.1, 4.0, 1)]
    seg = Segment(0.0, 4.0, "Hello there. Hi back.", "en",
                  words((0.1, 0.5, " Hello"), (0.6, 1.2, " there."), (2.2, 2.6, " Hi"), (2.7, 3.2, " back.")))
    out = assign_loop_segments([seg], turns)
    assert [(u.speaker, u.text) for u in out] == [("spk1", "Hello there."), ("spk2", "Hi back.")]
    assert out[1].start == 2.2


def test_unassigned_words_follow_neighbors():
    turns = [Turn(0.0, 1.0, 0), Turn(10.0, 11.0, 1)]
    seg = Segment(0.0, 3.0, "a b c", "en", words((0.2, 0.4, " a"), (0.9, 1.0, " b"), (1.2, 1.4, " c")))
    out = assign_loop_segments([seg], turns)
    assert [(u.speaker, u.text) for u in out] == [("spk1", "a b c")]


def test_short_flip_is_smoothed():
    turns = [Turn(0.0, 1.0, 0), Turn(1.0, 1.3, 1), Turn(1.3, 3.0, 0)]
    seg = Segment(0.0, 3.0, "one two three four", "en",
                  words((0.1, 0.5, " one"), (1.05, 1.25, " two"), (1.5, 2.0, " three"), (2.1, 2.5, " four")))
    out = assign_loop_segments([seg], turns)
    assert [(u.speaker, u.text) for u in out] == [("spk1", "one two three four")]


def test_no_turns_means_single_participant():
    seg = Segment(0.0, 1.0, "hello", "en", words((0.1, 0.5, " hello")))
    out = assign_loop_segments([seg], [])
    assert [(u.speaker, u.text) for u in out] == [("spk1", "hello")]


def test_far_segment_without_turn_is_unknown():
    turns = [Turn(0.0, 1.0, 0)]
    seg = Segment(20.0, 21.0, "far", "en", [])
    out = assign_loop_segments([seg], turns)
    assert out[0].speaker == UNKNOWN


def test_mic_join_and_merge_order():
    mic = mic_utterances([Segment(0.0, 1.0, "A1", "en"), Segment(1.5, 2.0, "A2", "en"), Segment(10.0, 11.0, "A3", "en")])
    assert [u.text for u in mic] == ["A1 A2", "A3"]
    loop = [Utterance(2.5, 4.0, "spk1", "B1"), Utterance(10.0, 12.0, "spk2", "C1")]
    merged = merge_tracks(mic, loop)
    assert [(u.speaker, u.text) for u in merged] == [(ME, "A1 A2"), ("spk1", "B1"), (ME, "A3"), ("spk2", "C1")]


def test_join_does_not_merge_across_other_speaker():
    utts = [Utterance(0, 1, "spk1", "a"), Utterance(1.2, 1.5, "spk2", "b"), Utterance(1.6, 2, "spk1", "c")]
    assert [u.text for u in join_utterances(utts)] == ["a", "b", "c"]


def test_speakers_in_and_representative():
    utts = [Utterance(0, 1, "spk10", "x"), Utterance(0, 1, "spk2", "short"),
            Utterance(5, 6, "spk2", "a much longer sentence here"), Utterance(9, 9.5, "spk2", "mid one"),
            Utterance(1, 2, ME, "me"), Utterance(3, 4, UNKNOWN, "?")]
    assert speakers_in(utts) == ["spk2", "spk10", UNKNOWN]
    reps = representative(utts, "spk2", 2)
    assert [r.text for r in reps] == ["a much longer sentence here", "mid one"]
    long = [Utterance(0, 1, "spk1", "w" * 500)]
    assert representative(long, "spk1")[0].text.endswith("…")


def test_speaker_boundary_follows_sentence_end():
    turns = [Turn(0.0, 2.05, 0), Turn(2.05, 5.0, 1)]
    seg = Segment(0.0, 5.0, "x", "en", words((0.1, 0.5, " Great."), (0.6, 1.9, " Thanks."), (1.95, 2.1, " I"),
                                            (2.3, 2.6, " can"), (2.7, 3.0, " help.")))
    out = assign_loop_segments([seg], turns)
    assert [(u.speaker, u.text) for u in out] == [("spk1", "Great. Thanks."), ("spk2", "I can help.")]


def test_long_monologue_is_split_into_lines():
    utts = [Utterance(i * 5.0, i * 5.0 + 4.5, "spk1", f"sentence number {i}.") for i in range(40)]  # 200초 독백
    out = join_utterances(utts)
    assert len(out) >= 4
    assert all(u.end - u.start <= 60.0 for u in out)
    assert " ".join(u.text for u in out) == " ".join(u.text for u in utts)
    many = [Utterance(i * 0.5, i * 0.5 + 0.4, "spk1", "w" * 100) for i in range(20)]  # 글자 수 상한
    assert all(len(u.text) <= 600 for u in join_utterances(many))
