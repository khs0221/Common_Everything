"""exe 에서 빼는 PyAV(av)·huggingface_hub 가 없어도 faster-whisper 를 불러올 수 있어야 합니다."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CODE = r"""
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('av', 'huggingface_hub', 'onnxruntime'):
            raise ImportError('blocked ' + name)
        return None
sys.meta_path.insert(0, Block())
sys.path.insert(0, ROOT)
from mrec.transcribe import _stub_optional_imports
_stub_optional_imports()
import faster_whisper
from faster_whisper import WhisperModel
print('OK', faster_whisper.__version__)
"""


def test_faster_whisper_imports_without_av_and_hub():
    r = subprocess.run([sys.executable, "-c", CODE.replace("ROOT", repr(str(ROOT)))],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "OK" in r.stdout
