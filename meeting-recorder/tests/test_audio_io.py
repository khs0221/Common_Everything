import numpy as np
import soundfile as sf

from mrec.audio_io import (Resampler, WavTrackWriter, float_to_int16, is_16k_mono_wav,
                           prepare_16k_mono, read_segment)


def test_float_to_int16_clips():
    y = float_to_int16(np.array([-2.0, -1.0, 0.0, 0.5, 1.0, 3.0], dtype=np.float32))
    assert y.tolist() == [-32767, -32767, 0, 16383, 32767, 32767]


def test_writer_file_readable_before_close(tmp_path):
    """비정상 종료 대비: 닫기 전에도 그때까지 쓴 내용이 정상 WAV 로 읽혀야 합니다."""
    p = tmp_path / "a.wav"
    w = WavTrackWriter(p, 16000)
    w.write(np.full(16000, 0.25, dtype=np.float32))
    w.write(np.full(8000, -0.25, dtype=np.float32))
    # close() 를 부르지 않은 상태(프로세스가 죽은 것과 같은 상황)
    data, sr = sf.read(str(p), dtype="float32")
    assert sr == 16000
    assert len(data) == 24000
    assert abs(data[100] - 0.25) < 1e-3 and abs(data[-1] + 0.25) < 1e-3
    w.close()
    data, _ = sf.read(str(p), dtype="float32")
    assert len(data) == 24000


def test_writer_silence_and_seconds(tmp_path):
    w = WavTrackWriter(tmp_path / "s.wav", 16000)
    w.write_silence(40000)
    assert w.frames_written == 40000 and abs(w.seconds - 2.5) < 1e-9
    w.close()
    assert sf.info(str(tmp_path / "s.wav")).frames == 40000


def _tone(sr, f, secs, amp=0.5):
    t = np.arange(int(sr * secs)) / sr
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def test_resampler_streaming_equals_oneshot_and_frequency():
    for sr in (48000, 44100, 22050, 8000):
        x = _tone(sr, 1000, 2.0)
        r1 = Resampler(sr)
        y1 = np.concatenate([r1.process(x), r1.process(np.zeros(0, np.float32), final=True)])
        r2 = Resampler(sr)
        parts, i, rng = [], 0, np.random.default_rng(0)
        while i < len(x):
            n = int(rng.integers(1, 3000))
            parts.append(r2.process(x[i:i + n]))
            i += n
        parts.append(r2.process(np.zeros(0, np.float32), final=True))
        y2 = np.concatenate(parts)
        assert len(y1) == int(np.ceil(len(x) * 16000 / sr))
        assert np.allclose(y1, y2, atol=1e-6)
        seg = y1[4000:20000]
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        peak_hz = np.argmax(spec) * 16000 / len(seg)
        assert abs(peak_hz - 1000) < 5
        assert 0.45 < np.abs(seg).max() < 0.55


def test_resampler_removes_alias():
    # 48kHz 의 12kHz 소리는 16kHz 로 바꾸면 들리면 안 됩니다(4kHz 로 접혀 들어오면 안 됨)
    x = _tone(48000, 12000, 1.0)
    r = Resampler(48000)
    y = np.concatenate([r.process(x), r.process(np.zeros(0, np.float32), final=True)])
    assert np.abs(y[2000:-2000]).max() < 0.01


def test_prepare_16k_mono(tmp_path):
    src = tmp_path / "st48.wav"
    st = np.stack([_tone(48000, 500, 1.0), _tone(48000, 500, 1.0)], axis=1)
    sf.write(str(src), st, 48000, subtype="PCM_16")
    out = prepare_16k_mono(src, tmp_path / "out.wav")
    assert out != src and is_16k_mono_wav(out)
    assert abs(sf.info(str(out)).frames - 16000) <= 1
    same = tmp_path / "m16.wav"
    sf.write(str(same), _tone(16000, 500, 1.0), 16000, subtype="PCM_16")
    assert prepare_16k_mono(same, tmp_path / "x.wav") == same
    flac = tmp_path / "a.flac"
    sf.write(str(flac), _tone(16000, 500, 1.0), 16000)
    assert is_16k_mono_wav(prepare_16k_mono(flac, tmp_path / "f.wav"))


def test_read_segment(tmp_path):
    p = tmp_path / "r.wav"
    x = np.linspace(-0.5, 0.5, 32000).astype(np.float32)
    sf.write(str(p), x, 16000, subtype="FLOAT")
    y = read_segment(p, 0.5, 1.0)
    assert len(y) == 8000 and abs(y[0] - x[8000]) < 1e-6
    assert len(read_segment(p, 1.9, 5.0)) == 1600
    assert len(read_segment(p, 3.0, 4.0)) == 0
