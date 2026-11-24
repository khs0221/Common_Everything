# 시험용 표본 음성

- 실제 회의가 아닌 **합성음**입니다. 대본은 `tools/make_sample.py` 안에 있는 가상 회의입니다.
- `sample_mic.flac`: '나'(마이크 트랙), `sample_loop.flac`: 참석자 2명(스피커 트랙), `sample_truth.json`: 발언자·시각 정답.
- 16kHz 모노, 약 42초. 아주 작은 잡음을 섞었습니다.
- 음성 모델: piper `en_US-libritts_r-medium` (sherpa-onnx tts-models 배포판). 모델 카드 표기 데이터 라이선스 CC BY 4.0
  (LibriTTS-R, https://www.openslr.org/141/). 자세한 내용은 `docs/라이선스.md`.
