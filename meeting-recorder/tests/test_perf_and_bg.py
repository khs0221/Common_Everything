"""1단계(VAD 패킹 화자 분리·온도 폴백 축소) 및 2단계 A(녹음 중 백그라운드 미리 전사) 시험."""
from types import SimpleNamespace as NS

import numpy as np
import soundfile as sf

from mrec import bg_transcribe, diarize, pipeline, transcribe, vad
from mrec.recorder import LOOP_FILE, MIC_FILE

SR = 16000


def test_diarize_vad_packing_skips_silence_and_restores_timestamps(tmp_path, monkeypatch):
    """말소리 구간만 패킹해 화자 분리를 돌리고, 결과 시각은 원래 녹음 시각으로 정확히 복원합니다."""
    wav = tmp_path / "loop.wav"
    sf.write(wav, np.zeros(SR * 20, dtype=np.float32), SR)

    seen_len = {}

    class FakeSD:
        sample_rate = SR

        def process(self, audio, callback=None):
            seen_len["sec"] = len(audio) / SR

            class Res:
                def sort_by_start_time(self):
                    # 패킹된 오디오: [0.0..2.0] = 원본 [2.0..4.0], 0.6초 무음, [2.6..4.6] = 원본 [14.0..16.0]
                    return [NS(start=0.2, end=1.8, speaker=0), NS(start=2.8, end=4.4, speaker=1)]

            return Res()

    monkeypatch.setattr(diarize, "make_diarizer", lambda *a, **k: FakeSD())
    turns = diarize.diarize_file(wav, speech=[(2.0, 4.0), (14.0, 16.0)])
    assert seen_len["sec"] < 6.0  # 20초 전체가 아니라 패킹된 ~4.6초만 처리
    assert len(turns) == 2
    assert abs(turns[0].start - 2.2) < 0.02 and abs(turns[0].end - 3.8) < 0.02 and turns[0].speaker == 0
    assert abs(turns[1].start - 14.2) < 0.02 and abs(turns[1].end - 15.8) < 0.02 and turns[1].speaker == 1


def test_temperature_fallback_is_two_stages():
    assert transcribe.Transcriber.TEMPERATURES == (0.0, 0.2)


def test_choose_cut_avoids_mid_speech_split():
    # 끝까지 말이 이어지면 직전 무음 경계에서 끊음
    segs, cut = bg_transcribe._choose_cut([(1.0, 5.0), (8.0, 10.0)], 10.0)
    assert segs == [(1.0, 5.0)] and cut == 5.0
    # 끝에 무음 여유가 있으면 끝까지 다 포함
    segs2, cut2 = bg_transcribe._choose_cut([(1.0, 5.0), (6.0, 8.0)], 10.0)
    assert segs2 == [(1.0, 5.0), (6.0, 8.0)] and cut2 == 10.0


def test_background_transcriber_and_pipeline_tail_merge(tmp_path, monkeypatch):
    """녹음 중 미리 전사된 구간은 재사용하고, pipeline.run 은 남은 꼬리 구간만 전사해 합칩니다."""
    folder = tmp_path / "meet"
    folder.mkdir()
    audio = (0.05 * np.sin(2 * np.pi * 440 * np.arange(SR * 6) / SR)).astype(np.float32)
    sf.write(folder / MIC_FILE, audio, SR)
    sf.write(folder / LOOP_FILE, audio, SR)
    monkeypatch.setenv("MREC_GLOSSARY_DIR", str(tmp_path / "g"))
    (tmp_path / "g").mkdir()

    def fake_vad(wav_path, *a, **k):
        _, _, frames = bg_transcribe.wav_info(wav_path)
        dur = frames / SR
        return [(0.2, min(dur - 0.5, 1.5))] if dur >= 1.0 else []

    monkeypatch.setattr(vad, "speech_segments", fake_vad)

    transcribe_calls = []

    class FakeTr:
        def __init__(self, *a, **k):
            self.dropped = []

        def transcribe_file(self, wav16k, speech, lang, **k):
            _, _, frames = bg_transcribe.wav_info(wav16k)
            transcribe_calls.append(round(frames / SR, 1))
            return [transcribe.Segment(a, b, "hello", "en", [transcribe.Word(a, b, " hello", 0.9)]) for a, b in speech]

    monkeypatch.setattr(transcribe, "Transcriber", FakeTr)

    bg = bg_transcribe.BackgroundTranscriber(
        folder, language="en", model="turbo", mic_min_db=-40.0,
        chunk_seconds=2.5, poll_seconds=0.05, safe_lag_seconds=0.2)
    bg.start()
    import time

    t0 = time.monotonic()
    while time.monotonic() - t0 < 5.0 and bg.chunks_done < 2:
        time.sleep(0.05)
    pre = bg.stop()
    assert pre is not None and pre.has_any()
    assert pre.tracks["mic"].done_up_to >= 5.0
    assert pre.tracks["loop"].done_up_to >= 5.0

    # 이제 파일을 9초로 늘려(남은 꼬리 3.2초) pipeline.run 호출: 꼬리 구간만 전사되어야 함
    audio9 = (0.05 * np.sin(2 * np.pi * 440 * np.arange(SR * 9) / SR)).astype(np.float32)
    sf.write(folder / MIC_FILE, audio9, SR)
    sf.write(folder / LOOP_FILE, audio9, SR)
    transcribe_calls.clear()

    diarize_speech = {}

    def fake_diarize(wav16k, num_speakers=None, progress=None, cancel=None, speech=None):
        diarize_speech["segs"] = speech
        return [diarize.Turn(0.0, 9.0, 0)]

    monkeypatch.setattr(diarize, "diarize_file", fake_diarize)

    job = pipeline.Job(folder, folder / MIC_FILE, folder / LOOP_FILE, language="en",
                       num_speakers=None, model="turbo", mic_min_db=-40.0, pre_transcribed=pre)
    res = pipeline.run(job)
    # 꼬리(~3.2초)만 전사됨 (9초 전체가 아님)
    assert len(transcribe_calls) == 2 and all(d < 4.0 for d in transcribe_calls), transcribe_calls
    # 화자 분리에는 미리 처리된 구간 + 꼬리 구간 전체가 전달됨
    assert len(diarize_speech["segs"]) == 2
    assert res.utterances >= 2
