"""감사용 독립 확인: (A) 장치 열기 지연이 있을 때 두 트랙의 위치 차이, (B) 루프백 무음 중 중지 시 지연.
실행: python verify_recorder.py  (meeting-recorder 폴더에서, 가짜 장치만 사용)"""
import sys, time, tempfile, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
import numpy as np, soundfile as sf
from mrec.devices import FakeBackend, FakeSource
from mrec.recorder import Recorder
SR = 16000

def clicky(h, click):
    def f(start, n):
        t = h[0]._start + (start + np.arange(n)) / SR
        y = np.zeros(n, np.float32); y[(t >= click) & (t < click + 0.02)] = 0.8
        return y
    return f

def first(path):
    x, sr = sf.read(str(path), dtype="float32"); i = np.flatnonzero(np.abs(x) > 0.5)
    return (i[0] / sr if len(i) else None), len(x) / sr

# A: 마이크를 먼저 열고 루프백은 0.4초 늦게 열림(실기기에서는 COM 초기화·장치 열기에 걸리는 시간)
tmp = pathlib.Path(tempfile.mkdtemp())
click = time.monotonic() + 2.0
mh, lh = [None], [None]
def mk_mic():
    mh[0] = FakeSource(SR, clicky(mh, click)); return mh[0]
def mk_loop():
    time.sleep(0.4); lh[0] = FakeSource(SR, clicky(lh, click)); return lh[0]
rec = Recorder(FakeBackend(mic=mk_mic, loop=mk_loop)); rec.start(tmp, None, None)
time.sleep(3.0); res = rec.stop()
cm, _ = first(res.mic_path); cl, _ = first(res.loop_path)
print(f"A: 같은 순간의 딸깍 위치 mic={cm:.3f}s loop={cl:.3f}s 차이={abs(cm-cl):.3f}s (0.15 초과면 어긋남)")

# B: 스피커 쪽이 무음(데이터 안 옴)인 채로 중지
tmp = pathlib.Path(tempfile.mkdtemp())
rec = Recorder(FakeBackend(loop=lambda: FakeSource(SR, lambda s, n: np.full(n, 0.1, np.float32), gaps=[(0.5, 100.0)])))
rec.start(tmp, None, None); time.sleep(1.5)
t = time.monotonic(); res = rec.stop(); dt = time.monotonic() - t
alive = rec.tracks["loop"].thread.is_alive()
print(f"B: 무음 중 중지에 걸린 시간 {dt:.2f}s, 읽기 스레드 아직 살아 있음={alive}, loop 장치 close 호출됨={rec.tracks['loop'].source._closed.is_set()}")
