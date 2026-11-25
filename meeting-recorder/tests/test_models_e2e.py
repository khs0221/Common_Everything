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
    res = pipeline.run(pipeline.Job(folder, folder / "mic.flac", folder / "loop.flac", model="turbo"))
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
    res = pipeline.run(pipeline.Job(folder, None, samples / "sample_loop.flac", language="en", num_speakers=2,
                                    model="turbo"))
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
        pipeline.run(pipeline.Job(folder, samples / "sample_mic.flac", samples / "sample_loop.flac", model="turbo"),
                     cancel=cancel)
    assert not (folder / output.RECORD_MD).exists()


def test_selftest_passes(tmp_path):
    from mrec import selftest

    out = tmp_path / "r.json"
    rc = selftest.run(str(out))
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0, rep.get("failed_summary")  # 실패하면 어느 항목인지 시험 결과에 바로 보임
    assert rep["ok"] and rep["peak_memory_mb"] > 0 and rep["failed_summary"] == []


def test_mixed_language_track_keeps_each_language(samples, tmp_path):
    """한 트랙 안에서 한국어 → 영어 → 한국어로 바뀌어도, 영어는 영어로·한국어는 한글로 적혀야 합니다(R3).
    R2 코드에서는 같은 표본에서 영어 발언이 한국어로 번역되거나 빠졌습니다(changes.md R3 재현 기록)."""
    from mrec import selftest

    folder = tmp_path / "mix"
    folder.mkdir()
    shutil.copy(samples / "sample_mixed_lang.flac", folder / "mix.flac")
    pipeline.run(pipeline.Job(folder, folder / "mix.flac", None, language=None))
    truth = json.loads((samples / "sample_mixed_lang_truth.json").read_text(encoding="utf-8"))
    utts, _, _ = output.load_result_json(folder)
    sc = selftest.score_languages(truth, utts)
    assert sc["en_as_hangul"] == 0 and sc["ok"] == sc["n"], sc
    assert {u.lang for u in utts} == {"en", "ko"}


def test_fixed_language_skips_detection(samples, tmp_path):
    """'영어'로 고정하면 언어 판별을 하지 않고 모두 영어로 처리합니다(기존 동작 유지)."""
    folder = tmp_path / "fixed"
    folder.mkdir()
    pipeline.run(pipeline.Job(folder, samples / "sample_mic.flac", None, language="en", model="turbo"))
    utts, meta, _ = output.load_result_json(folder)
    assert meta["language"] == "en" and {u.lang for u in utts} == {"en"}


def test_turbo_default_with_small_language_detection_and_hints(samples, tmp_path):
    """기본(large-v3-turbo) + 언어 판별 small + 용어 힌트: 혼합 언어 표본이 그대로 통과하고 meta 가 맞아야 합니다(R4)."""
    from mrec import selftest

    folder = tmp_path / "med"
    folder.mkdir()
    shutil.copy(samples / "sample_mixed_lang.flac", folder / "mix.flac")
    job = pipeline.Job(folder, folder / "mix.flac", None, language=None)
    assert job.model == "turbo" and job.lid_model == "small"
    pipeline.run(job)
    utts, meta, _ = output.load_result_json(folder)
    mtruth = json.loads((samples / "sample_mixed_lang_truth.json").read_text(encoding="utf-8"))
    sc = selftest.score_languages(mtruth, utts)
    assert sc["en_as_hangul"] == 0 and sc["ok"] == sc["n"], sc
    assert selftest.extra_sentences(mtruth, utts) == []  # 감사 R6 I2: 끝에 "감사합니다." 가 덧붙지 않음
    assert meta["model"] == "turbo" and "language_detection" in meta
    for name in (output.RECORD_MD, output.TRANSCRIPT_TXT, output.COPILOT_TXT, output.GUIDE_MD):
        assert (folder / name).is_file()


def test_no_glossary_files_still_works(samples, tmp_path, monkeypatch):
    monkeypatch.setenv("MREC_GLOSSARY_DIR", str(tmp_path / "none"))
    folder = tmp_path / "ng"
    folder.mkdir()
    pipeline.run(pipeline.Job(folder, samples / "sample_mic.flac", None, language="en", model="turbo"))
    utts, meta, _ = output.load_result_json(folder)
    assert utts and "hints" not in meta
    assert "=== 용어 목록 시작 ===" not in (folder / output.COPILOT_TXT).read_text(encoding="utf-8-sig")


def test_short_utterance_language_detection(samples):
    """감사 R6 I1: 1초·1.5초·2초 짧은 발언(한국어·영어)의 언어 판별(판별 모델 small, 기준 0.6).

    혼합 언어 표본의 정답 발언마다 앞에서부터 같은 길이 조각을 최대 3개 잘라, 조각 하나만으로 판별합니다
    (이웃 조각이 없는 가장 나쁜 경우). 수정 1회차 측정: small 은 영어 조각을 한국어로 판별한 것이 0건,
    '확실한데 틀림' 0건, 2초 조각은 모두 맞음. base(0.7~0.9)는 1초·1.5초에서 영어를 한국어로 1건씩 판별해
    이 시험을 통과하지 못합니다(사용자가 겪은 '영어가 한글로 적힘' 방향).
    """
    import soundfile as sf

    from mrec import langid, models
    from mrec.paths import models_dir
    from mrec.transcribe import Transcriber

    lid = pipeline.LID_MODEL
    assert lid == "small"
    tr = Transcriber(model_dir=models_dir() / models.WHISPER_DIRS[lid], model=lid)
    probe = langid.WhisperLangProbe(tr.model)
    conf = langid.CONF_BY_MODEL[lid]
    x, sr = sf.read(samples / "sample_mixed_lang.flac", dtype="float32")
    assert sr == 16000
    truth = json.loads((samples / "sample_mixed_lang_truth.json").read_text(encoding="utf-8"))
    res = {}
    for length in (1.0, 1.5, 2.0):
        r = {"ko": [0, 0, 0], "en": [0, 0, 0]}  # 조각 수, 틀림, 확실한데 틀림
        n = int(length * sr)
        for t in truth:
            a, b = int(t["start"] * sr), int(t["end"] * sr)
            for k in range(3):
                s0 = a + k * n
                if s0 + n > b:
                    break
                pieces = langid.split_segment(x[s0:s0 + n], 0.0, length, probe, conf)
                filled = langid.fill_unsure(pieces)
                q = r[t["lang"]]
                q[0] += 1
                q[1] += filled[0].lang != t["lang"]
                q[2] += pieces[0].sure and pieces[0].lang != t["lang"]
        res[length] = r
    for length, r in res.items():
        assert r["ko"][0] >= 8 and r["en"][0] >= 8, res
        assert r["en"][1] == 0, (length, res)  # 영어를 한국어로(한글로 적힘) 0건
        assert r["ko"][2] == 0 and r["en"][2] == 0, (length, res)  # 확실한데 틀림 0건(이웃으로도 못 고침)
    assert res[2.0]["ko"][1] == 0 and res[2.0]["en"][1] == 0, res
    assert res[1.0]["ko"][1] <= 5 and res[1.5]["ko"][1] <= 2, res  # 수정 1회차 측정값(더 나빠지지 않게)
