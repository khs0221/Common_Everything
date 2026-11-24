"""모델을 실제로 돌리는 시험(합성 표본 음성). 모델이 없으면 '실행 안 함'으로 따로 표시됩니다."""
import json
import shutil

import pytest

from mrec import output, pipeline
from mrec.audio_io import prepare_16k_mono

pytestmark = pytest.mark.models


@pytest.fixture(scope="module")
def truth(request):
    from pathlib import Path

    p = Path(__file__).resolve().parent.parent / "samples" / "sample_truth.json"
    return json.loads(p.read_text(encoding="utf-8"))


def test_vad_finds_each_utterance(samples, tmp_path, truth):
    from mrec import vad

    wav = prepare_16k_mono(samples / "sample_loop.flac", tmp_path / "l.wav")
    segs = vad.speech_segments(wav)
    loop_truth = [t for t in truth if t["track"] == "loop"]
    for t in loop_truth:
        covered = sum(max(0.0, min(b, t["end"]) - max(a, t["start"])) for a, b in segs)
        assert covered > 0.8 * (t["end"] - t["start"]), t


def test_diarization_matches_truth(samples, tmp_path, truth):
    from mrec import diarize

    wav = prepare_16k_mono(samples / "sample_loop.flac", tmp_path / "l.wav")
    turns = diarize.diarize_file(wav)
    assert len({t.speaker for t in turns}) == 2
    loop_truth = [t for t in truth if t["track"] == "loop"]
    mapping = {}
    for u in loop_truth:
        ov = {}
        for t in turns:
            ov[t.speaker] = ov.get(t.speaker, 0) + max(0.0, min(u["end"], t.end) - max(u["start"], t.start))
        best = max(ov, key=ov.get)
        mapping.setdefault(u["speaker"], set()).add(best)
    # 사람마다 하나의 번호, 서로 다른 번호
    assert all(len(v) == 1 for v in mapping.values())
    assert len({next(iter(v)) for v in mapping.values()}) == 2


def test_full_pipeline_on_sample(samples, tmp_path, truth):
    folder = tmp_path / "회의_시험"
    folder.mkdir()
    shutil.copy(samples / "sample_mic.flac", folder / "mic.flac")
    shutil.copy(samples / "sample_loop.flac", folder / "loop.flac")
    res = pipeline.run(pipeline.Job(folder, folder / "mic.flac", folder / "loop.flac"))
    assert (folder / output.RECORD_MD).is_file() and (folder / output.COPILOT_TXT).is_file()
    assert not (folder / "_작업중").exists()
    utts, meta, _ = output.load_result_json(folder)
    assert res.speakers == ["spk1", "spk2"]
    # 발언 순서: 정답의 발언자 순서(같은 사람 연속은 하나로)와 같아야 함
    names = {"me": "me"}
    seq = []
    for u in utts:
        if not seq or seq[-1] != u.speaker:
            seq.append(u.speaker)
    exp = []
    first = {}
    for t in truth:
        key = "me" if t["speaker"] == "me" else first.setdefault(t["speaker"], f"spk{len(first) + 1}")
        if not exp or exp[-1] != key:
            exp.append(key)
    assert seq == exp, (seq, exp)
    text = " ".join(u.text for u in utts).lower()
    for w in ("finance", "workshop", "thursday", "budget", "friday", "march"):
        assert w in text, w
    assert meta["mode"] == "two_tracks" and meta["language"] == "auto"
    assert all(u.lang == "en" for u in utts)


def test_single_file_mode_and_forced_language(samples, tmp_path):
    folder = tmp_path / "one"
    folder.mkdir()
    res = pipeline.run(pipeline.Job(folder, None, samples / "sample_loop.flac", language="en", num_speakers=2))
    assert res.speakers == ["spk1", "spk2"]
    utts, meta, _ = output.load_result_json(folder)
    assert meta["mode"] == "single_file" and meta["language"] == "en"
    assert not any(u.speaker == "me" for u in utts)


def test_cancel_stops_processing(samples, tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(InterruptedError):
        pipeline.run(pipeline.Job(folder, samples / "sample_mic.flac", samples / "sample_loop.flac"), cancel=cancel)
    assert not (folder / output.RECORD_MD).exists()


def test_selftest_passes(tmp_path):
    from mrec import selftest

    out = tmp_path / "r.json"
    rc = selftest.run(str(out))
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0, rep.get("failed_summary")  # 실패하면 어느 항목인지 시험 결과에 바로 보임
    assert rep["ok"] and rep["peak_memory_mb"] > 0 and rep["failed_summary"] == []
