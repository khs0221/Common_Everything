# 시험용 표본 음성

## 회의 표본(합성)

- 실제 회의가 아닌 **합성음**입니다. 대본은 `tools/make_sample.py` 안에 있는 가상 회의입니다.
- `sample_mic.flac`: '나'(마이크 트랙), `sample_loop.flac`: 참석자 2명(스피커 트랙), `sample_truth.json`: 발언자·시각 정답.
- 16kHz 모노, 약 42초. 아주 작은 잡음을 섞었습니다.
- 음성 모델: piper `en_US-libritts_r-medium` (sherpa-onnx tts-models 배포판). 모델 카드 표기 데이터 라이선스 CC BY 4.0
  (LibriTTS-R, https://www.openslr.org/141/). 자세한 내용은 `docs/라이선스.md`.

## 혼합 언어 표본(R3)

- `sample_mixed_lang.flac`(약 62초, 16kHz 모노), `sample_mixed_lang_truth.json`(발언별 언어·시각·출처).
- 한 트랙 안에서 한국어 3문장 → 영어 3문장 → 한국어 2문장 → 영어 3문장 → 한국어 1문장 순서로, 0.3초 간격으로 이어 붙였습니다.
  같은 사람이 쉬지 않고 언어를 바꾸는 흐름을 흉내 낸 것이며, 실제로는 문장마다 다른 화자입니다.
- 출처: Google FLEURS(https://huggingface.co/datasets/google/fleurs) ko_kr·en_us dev 분할, **CC BY 4.0**.
  파일 이름은 truth 의 `source` 에 있습니다. 변경: 앞뒤 무음 자르기, 크기 맞추기, 이어 붙이기.
  영어 1문장은 위 합성 표본(`sample_mic.flac`)의 첫 문장입니다.
- 만드는 도구: `tools/make_mixed_sample.py`.
