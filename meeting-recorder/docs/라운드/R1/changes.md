# R1 changes — 회의 녹음·기록 프로그램 골조

작성: engineer, 2026-11-24. 브랜치 `claude/wizardly-lovelace-ojns73`. 시작 커밋 9454981.

## 1. 한 일 (요약)

| 범위(계획서 3절) | 결과 | 위치 |
|---|---|---|
| 1. 녹음(두 트랙, 시간축 맞춤, 흘려 쓰기, 비정상 종료 대비) | 구현 + 가짜 장치 시험. **윈도우 실제 장치는 미검증** | `mrec/devices.py`, `mrec/recorder.py`, `mrec/audio_io.py` |
| 2. 글로 옮기기(faster-whisper small, CPU int8, 자동/영어/한국어) | 구현 + 컨테이너 실행 검증(영어 합성 표본, 한국어 공개 음성) | `mrec/transcribe.py`, `mrec/vad.py` |
| 3. 발언자 구분(마이크=나, 스피커 트랙=sherpa-onnx 화자 분리) | 구현 + 실행 검증 | `mrec/diarize.py`, `mrec/merge.py` |
| 4. 결과물(기록.md, Copilot용.txt, 이름 지정) | 구현 + 실행 검증 | `mrec/output.py`, `mrec/gui.py`(이름 창) |
| 5. 화면(tkinter 한 창, 한국어) | 구현. 리눅스 Xvfb 에서 가짜 장치로 녹음·중지·처리 확인, 화면 캡처 확인. **윈도우 화면은 미검증** | `mrec/gui.py`, `app.py` |
| 6. 배포(PyInstaller onedir + 모델 동봉, `--selftest`, Actions) | 작성. 리눅스에서 같은 spec 으로 빌드한 실행 파일의 `--selftest` 통과. **윈도우 CI 실행 결과는 미확인(총괄 확인)** | `MeetingRecorder.spec`, `.github/workflows/meeting-recorder-build.yml`, `tools/download_models.py` |

기타: 실행 중 외부 접속 차단(`mrec/offline.py`, 소켓 연결·이름 조회를 막음), 합성 표본 음성(`samples/`, `tools/make_sample.py`),
라이선스 표(`docs/라이선스.md`), 사용자 README.

## 2. 채택·기각한 기술과 근거

### 녹음: SoundCard 0.4.6 채택 (PyAudioWPatch 기각)
- 공식 소스(휠)를 직접 읽고 확인: WASAPI 루프백 지원(`get_microphone(스피커 id, include_loopback=True)`), 요청 표본 주파수로
  WASAPI 자동 변환 플래그(AUTOCONVERTPCM | SRC_DEFAULT_QUALITY) 사용 → 16kHz 모노를 바로 받을 수 있음(안 되면 48k/44.1k 로 물러나고
  처리 단계에서 자체 리샘플러로 16k 변환). 루프백 무음 시 데이터가 안 오는 문제를 경과 시간만큼 0 을 돌려주는 방식으로 처리함(0.4.6 소스 확인).
  numpy 2 호환(`frombuffer`). BSD-3. 순수 파이썬 + cffi 라 exe 묶기가 단순하고 PyInstaller 훅을 자체 포함.
- PyAudioWPatch(MIT, PortAudio 포크)도 휠은 있으나, 무음 처리·시간축은 어차피 직접 맞춰야 하고 PortAudio DLL 이 추가됨. 장점이 크지 않아 기각.
- 시간축 맞춤은 라이브러리에 맡기지 않고 직접 함: 조각 도착 시각과 쓴 표본 수를 비교해 0.25초 넘게 뒤처지면 무음을 채움(`AlignedTrack.write_block`).
  장치 오류 시 그 트랙은 중지 때까지 무음으로 채워 길이를 맞춤. 중지 때 두 트랙 길이를 같게 맞춤.
- 장치 읽기 스레드와 디스크 쓰기 스레드를 분리(디스크 지연 때 WASAPI 버퍼 넘침 방지), WASAPI 버퍼 0.5초.
- soundcard 는 불러오는 스레드를 COM MTA 로 초기화하므로, 화면 주 스레드가 아닌 별도 스레드에서 불러오고 장치를 엶(파일 선택 창 오작동 예방, `_run_in_mta`). **효과는 윈도우에서 미검증**.
- 파일 형식: 표준 `wave` 모듈로 16비트 모노 WAV 를 조각마다 쓰고, 쓸 때마다 머리말 길이를 고쳐 씀 + 1초 미만마다 flush, 5초마다 fsync.
  `close()` 없이 읽어도 정상 WAV(시험 `test_writer_file_readable_before_close`, `test_recording_survives_without_stop`).

### 글로 옮기기: faster-whisper 1.2.1 + Whisper small(다국어), CPU int8 채택
- 모델 크기 근거: small int8 처리 중 메모리 최고치 약 1.0~1.5GB(아래 측정), 내장 그래픽·CPU 전용에서 실용 속도.
  base 는 한국어 품질이 낮고, medium/large-v3-turbo 는 int8 이어도 메모리 2GB 안팎·속도 2~4배 느림(추정) → R2 선택지로 남김.
- beam_size=1(탐욕 탐색): 표본에서 beam 5 대비 약 25~40% 빠르고 결과 차이 작음(42초 표본: 마이크 4.4초 vs 5.3초, 스피커 5.9초 vs 8.1초).
- **faster-whisper 의 내장 VAD(onnxruntime 파이썬 패키지 필요)는 쓰지 않고**, sherpa-onnx 의 silero VAD 로 말소리 구간을 찾은 뒤
  말소리만 이어 붙여(최대 180초 묶음) whisper 에 넣고 시각을 되돌림(`TimeMap`). 이유:
  1) sherpa-onnx 가 자체 onnxruntime.dll 을 갖고 있어, onnxruntime 파이썬 패키지까지 넣으면 윈도우에서 같은 이름 DLL 이 두 개가 됨(충돌 위험).
  2) whisper 는 30초 창 단위 계산이라, 무음이 많은 트랙(듣고만 있는 마이크)을 구간마다 따로 넣으면 낭비가 큼(첫 구현 대비 마이크 9.4초→3.2초).
  3) 파일을 조각으로 읽어 2시간 녹음도 메모리가 일정(faster-whisper 는 전체 음성 특징을 한 번에 계산 → 2시간이면 수 GB).
- 이어 붙인 음성에서는 떨어진 말이 한 구간으로 나올 수 있어, 단어 시각으로 원래 구간별로 다시 나눔. 경계 근처 단어 시각 흔들림은 문장 부호로 바로잡음(`rebalance`).
- '자동' 은 영어/한국어 중에서만 고름: 묶음마다 whisper 판별 결과가 en/ko 가 아니면 en/ko 중 확률 높은 쪽으로 다시 돌림.
- exe 에서 PyAV(av)·huggingface_hub 제외: faster-whisper 가 파일 맨 위에서 불러오지만 우리는 배열·로컬 경로만 쓰므로 빈 모듈로 대신함.
  PyAV 휠의 FFmpeg 에 GPL 인 x264/x265 가 들어 있어 라이선스 위험 + 용량 70MB. 대신 음성 파일 읽기는 soundfile(libsndfile: WAV/FLAC/OGG/MP3).
  → m4a/mp4 는 '음성 파일 하나 처리' 에서 못 읽음(R2 후보).

### 화자 분리: sherpa-onnx 2.0.3 오프라인 화자 분리 채택
- 공식 문서(k2-fsa.github.io/sherpa/onnx/speaker-diarization)와 공식 파이썬 예제(`python-api-examples/offline-speaker-diarization.py`)의 API 를 그대로 씀.
  torch 불필요(onnxruntime 내장), Apache-2.0.
- 구간 나누기 모델: pyannote segmentation-3.0 int8(1.5MB, MIT). 목소리 특징 모델: 5종 비교 후 **3D-Speaker CAM++ VoxCeleb(영어)** 채택(29.6MB, Apache-2.0, ModelScope 라이선스 항목 확인).
  - 합성 표본(2명): 5종 모두 정답. 기준값 0.5/0.7/0.9 중 CAM++ 만 세 값 모두 정답(가장 안정).
  - 공식 문서의 실제 녹음 4인 표본(중국어, `0-four-speakers-zh.wav`, 저장소에 넣지 않음): CAM++ 기준값 0.7 에서 공식 문서의 출력(4명, 구간 10개)과 같은 결과. 0.5 에서는 5명으로 과분할.
  - 기준값 0.7 채택. 상대방 인원을 알면 화면에서 지정(군집 수 고정).
- 창 이동 비율 0.25(기본 0.1): 4인 표본·합성 표본 결과가 0.1 과 거의 같고 약 2.5배 빠름. 0.5 는 경계 오차가 커져 기각.
- 발언자 붙이기: 트랙 전체 단어를 문장(문장 부호 또는 1.5초 넘는 빈틈) 단위로 나누고, 문장마다 시각이 가장 많이 겹치는 참석자를 붙임.
  한 문장 안에서 다른 참석자 쪽 단어가 3개 이상·1초 이상 이어질 때만 나눔(단어 시각 흔들림으로 인한 한두 단어 오표시 방지).
  처음 구현(단어마다 붙임)에서 10분 시험 중 "…in March. I" / "can prepare…" 처럼 첫 단어가 앞사람에게 붙는 오류 2건 발견 → 고친 뒤 0건.

### 배포: PyInstaller 6.22.3 onedir, Python 3.12, windows-latest
- 창 모드 exe(`console=False`) 하나로 화면과 `--selftest` 를 모두 처리. 창 모드라 표준 출력이 없으므로 결과는 `--selftest-out` 파일(JSON).
  selftest 단계에서 예외가 나면 오류 창 대신 파일에 쓰고 종료(CI 가 멈추지 않게).
- 모델은 빌드 때 `tools/download_models.py` 로 받아 SHA-256 확인 후 `dist/MeetingRecorder/models` 로 복사. 실행 중에는 받지 않음.

## 3. 측정값 (리눅스 컨테이너: Intel Xeon 2.1GHz 4코어, 메모리 15GB, Python 3.12)

| 항목 | 값 |
|---|---|
| 표본 42.5초(2트랙, 3명) 전체 처리 | 11.1초(실시간의 0.26배). 화자 분리 1.7초, 모델 읽기 0.9초, 마이크 글옮기기 3.2초, 스피커 4.7초 |
| 같은 표본, PyInstaller 로 묶은 실행 파일 `--selftest` | 전체 13.3초, 처리 11.7초, 메모리 최고치 996MB |
| 10분(표본 반복) 전체 처리 | 231초(0.39배), 메모리 최고치 1,413~1,512MB |
| 30분(표본 반복) 전체 처리 | 754초(0.42배), 메모리 최고치 1,515MB |
| 화자 분리만 2시간(4인 실제 녹음 반복, 창 이동 0.25) | 263초(0.036배), 메모리 최고치 1,152MB(입력 배열 약 460MB 포함) |
| 한국어 공개 음성(FLEURS ko, CC BY 4.0, 저장소에 넣지 않음) 67초, 2트랙 | 13.4초. '자동' 에서 한국어로 판별. 일반 문장은 대체로 맞고 고유명사는 틀림(예: "하위문화"→"하이문화") |
| 리눅스 묶음 크기 | 압축 전 718MB(모델 495MB), zip(-6) 606MB |
| 윈도우 zip 예상 | 약 600~650MB(추정: 윈도우 휠 DLL 크기 합 + 모델) |

- 반복 표본의 10·30분 측정에서는 같은 문장이 반복돼 whisper 의 반복 방지 재시도(온도 올려 다시 풀기)가 마이크 트랙에서 자주 일어나 실제보다 느림(마이크 143초 vs 스피커 54초).
  실제 회의 속도는 42초 표본(0.26배)과 이 값(0.4배) 사이로 추정.
- 10분 반복 결과에서 키워드로 정답을 알 수 있는 발언 152개의 발언자 표시를 대조: 152개 모두 맞음(합성음이라 쉬운 조건).

### 8GB PC 에서 메모리 최고치 4GB 이하 근거
1. 글옮기기 단계 최고치는 녹음 길이와 무관하게 일정: 42초 986MB → 10분 1.4~1.5GB → 30분 1.5GB. 말소리를 180초 묶음으로 읽고 버리기 때문.
2. 화자 분리는 스피커 트랙 전체를 메모리에 올리지만 2시간에도 최고치 1.15GB(측정). 글옮기기보다 먼저 끝내고 비운 뒤 whisper 를 올림.
3. 두 단계가 겹치지 않으므로 2시간 회의 예상 최고치 약 1.5~2GB, 해제된 메모리가 운영체제로 돌아가지 않는 최악의 경우에도 합계 약 2.7GB.
   **(정정, 수정 1회차: 2시간 값은 측정이 아니라 30분 전체 측정 + 2시간 화자 분리 단독 측정을 더한 추정입니다.)**
4. 녹음 중 메모리는 0.1초 조각만 다루므로 녹음 길이와 무관(쓰기 대기열은 디스크 실패 시에도 비움).
- 한계: 리눅스 측정값. 윈도우 값은 CI selftest 의 `peak_memory_mb`(PeakWorkingSetSize, 42초 표본)로 확인 필요. 2시간 전체 파이프라인을 한 번에 돌린 측정은 없음(시간 제약, 30분까지).

## 4. 검증한 것 / 못 한 것

### 검증함(이 컨테이너에서 실제 실행)
- 패키지 설치·실행: faster-whisper 1.2.1, ctranslate2 4.9.1, sherpa-onnx 2.0.3, soundfile 0.14.0(Python 3.12, 3.13).
- 윈도우용 휠 존재: `pip download --platform win_amd64 --python-version 3.12` 로 전부 받아짐(ctranslate2, sherpa-onnx, sherpa-onnx-core, numpy, soundfile, soundcard, pyinstaller).
- 모델 다운로드: github.com releases, huggingface.co 모두 받아짐. 막힌 것: `github.com/k2-fsa/sherpa-onnx/releases` 목록 페이지와 `api.github.com` 은 403(다운로드 주소는 정상). 우회하지 않음.
- 글옮기기·화자 분리·병합·결과물 생성: 합성 표본(영어 3명)과 한국어 공개 음성으로 실행, `기록.md`·`Copilot용.txt` 생성 확인.
- 녹음 계층: 가짜 장치로 두 트랙 시간축(1초 무음 구간 + 장치 시계 0.5% 느림에서도 두 트랙의 같은 소리가 0.15초 안 **(정정: 장치 열기 시간이 0 인 가짜 장치에서만 성립했음. 감사 F3 으로 고침 → 수정 1회차 절 참고)**), 장치 오류 시 다른 트랙 유지·길이 일치, 디스크 쓰기 실패 보고, 중지 없이 죽어도 파일 읽힘, 장치 열기 실패 시 폴더 안 만듦.
- 리샘플러: 48k/44.1k/22.05k/8k → 16k, 조각 입력 = 한 번 입력, 1kHz 유지, 48k 의 12kHz 소리 제거(접힘 없음).
- 외부 접속 차단: 바깥 연결·이름 조회는 예외, 127.0.0.1 은 허용.
- PyInstaller: 리눅스에서 같은 spec 으로 빌드 → 실행 파일 `--selftest` 통과, `--fake-devices` 화면 8초 실행(오류 없음). av·huggingface_hub·onnxruntime 미포함 확인.
- 화면: Xvfb 에서 가짜 장치로 녹음 시작·막대 움직임·중지·자동 처리, 캡처 이미지로 배치 확인.

### 못 함(미검증)
- **윈도우 실제 장치**: **(정정: 처음 목록에 COM 스레드 초기화 문제(F1)와 장치 끊김·기본 장치 변경 시 다시 연결 없음(F2)이 빠져 있었음)** WASAPI 마이크·루프백 녹음, 16kHz 직접 요청 가능 여부, 루프백 무음 처리, 장치 이름 표시, COM 스레드 처리, 블루투스 헤드셋 전환, Teams 와 동시 사용.
- **윈도우 화면**: 글꼴·배치, 파일 선택 창, 고해상도(DPI) 표시, 절전 방지(SetThreadExecutionState).
- **윈도우 exe**: CI 빌드·selftest 결과(이 세션에서 gh 를 못 씀). **(정정: 계정의 Actions 무료 시간 소진으로 워크플로가 한 번도 시작되지 못했습니다(감사 F4). 윈도우 빌드 증거 없음.)** 한글 경로에서 모델 읽기(8.3 짧은 경로 변환)도 윈도우에서만 확인 가능.
- 실제 사람 목소리 영어 회의(겹쳐 말하기, 잡음, 원격 회의 코덱)에서의 정확도. 표본은 합성음이라 쉬운 조건.
- 사용자 PC(i5 급 노트북 추정) 처리 속도: 컨테이너 대비 1.5~2.5배 느릴 것으로 **추측**(실시간의 0.4~1배).
- Copilot 입력 길이 한도: 1시간 회의 기록은 수만 자라 붙여 넣기 한도를 넘을 수 있음(한도 수치 미확인).

## 5. 사용자 PC 에서 확인할 항목 (README 4절 '첫 시험' 과 같음)
1. zip 풀기 → exe 실행(SmartScreen, 회사 보안 정책 차단 여부).
2. 장치 목록에 헤드셋 마이크·스피커가 보이는지, 기본 장치가 미리 골라지는지.
3. 녹음 중 두 막대가 각각 움직이는지(유튜브 소리로 스피커 막대 확인).
4. 1분 녹음 → 처리 → `기록.md` 에 '나' 와 '참석자' 가 나뉘는지, 두 트랙 시각이 맞는지(내가 말한 직후 상대 말이 순서대로 나오는지).
5. 처리 시간(1분 녹음 기준), 작업 관리자의 메모리 최고치.
6. Teams 실제 회의 10분 시험(헤드셋), 상대방 2명 이상일 때 구분.
7. 경로에 한글이 있는 폴더에 풀었을 때 경고·동작.

## 6. CI 첫 실행에서 실패할 가능성이 큰 지점
1. **ctranslate2 DLL 로딩(동결 exe)**: `ctranslate2/__init__.py` 가 `importlib.resources.files("ctranslate2")` 폴더의 *.dll 을 직접 불러옴. spec 에서 `collect_dynamic_libs("ctranslate2")` 로 같은 폴더에 넣었지만 윈도우에서 확인 못 함. 실패 시 `--collect-all ctranslate2` 로 바꿔 볼 것.
2. **sherpa-onnx DLL**: `sherpa_onnx/lib/onnxruntime.dll`, `sherpa-onnx-c-api.dll` 을 `collect_dynamic_libs("sherpa_onnx")` 로 넣음. pyd 가 같은 폴더 DLL 을 찾는지 윈도우에서 미확인.
3. **soundcard 의 PyInstaller 훅**: 리눅스에서는 훅 진입점이 AssertionError(리눅스 pulseaudio 없음)로 실패. 윈도우에서는 정상일 것으로 예상하나 미확인. cffi 헤더(`mediafoundation.py.h`)가 빠지면 selftest 의 'soundcard 포함' 이 실패함.
4. **pytest 의 화면 시험(`test_gui.py`) 과 시간 의존 시험(`test_recorder.py`)**: 윈도우 러너에서 Tk 창 생성 가능 여부, 느린 러너에서 0.15초 허용치. 실패하면 시험 조정 필요(코드 문제가 아닐 수 있음).
5. **Start-Process 로 창 모드 exe 실행**: 종료 코드·결과 파일 경로 따옴표 처리. 15분 초과 시 강제 종료.
6. **VC++ 런타임(msvcp140.dll) 포함 여부**: 러너에는 설치돼 있어 selftest 는 통과해도 사용자 PC 에서 없으면 실패. 워크플로가 포함 여부를 경고로 출력함.
7. 첫 실행은 캐시가 없어 모델 495MB 를 받음(github releases, huggingface). 네트워크 일시 오류는 4회 재시도.
8. 아티팩트 약 600MB 업로드 시간.

## 7. 총괄이 결정할 것
1. **사용자 PC 처리 속도가 느리면**: beam_size(현재 1), 모델 크기(small 유지 / base 로 낮춤 / medium 선택지 추가). 실제 PC 측정 뒤 결정 권장.
2. **Copilot 입력 한도**: 긴 회의는 `Copilot용.txt` 를 여러 개로 나눌지(R2). 현재는 한 파일 + README 에 '파일 첨부' 안내.
3. **스피커로 들을 때 중복**: 헤드셋 권장만 했음. 마이크에 들어간 상대방 소리를 지우는 기능(에코 제거)은 R2 이후.
4. **m4a/mp4(Teams 녹화 파일 등) 처리**: PyAV 를 빼서 못 읽음. 필요하면 LGPL FFmpeg 별도 동봉 등 검토.
5. **표본 음성 라이선스**: piper 음성 모델이 lessac 음성에서 이어 학습된 것이라 엄격한 해석 시 원 음성 조건 확인 필요(`docs/라이선스.md`). 걱정되면 R2 에서 CC BY 실제 녹음(예: FLEURS)으로 바꿀 수 있음.
6. 배포 경로: 지금은 Actions 아티팩트(30일 보관, 깃허브 로그인 필요). 릴리스로 올릴지.
7. 녹음 동의 안내 문구(README 에 한 줄 넣음) 수준이 충분한지.

## 8. 시험 결과
- `python -m pytest -q` (Xvfb 화면 있음): **50 passed**, 0 failed.
- 화면 없이(DISPLAY 없음): 49 passed, 1 '실행 안 함'(화면 시험, skip 아님으로 따로 표시). **(정정: tkinter 가 설치된 환경에서만 성립. tkinter 가 없으면 selftest 시험 1개가 실패했음(감사 F10). 수정 1회차 결과는 아래 절)**
- 모델 없이: 43 passed, 7 '실행 안 함'(모델 필요 6 + 화면 1) — 시험 끝에 이유와 목록이 따로 출력됨.
- 골든 출력: 새 프로젝트라 해당 없음(baseline 참고).


## 수정 1회차 (감사 findings.md 반영, 2026-11-24)

### 판정 반영표

| ID | 총괄 판정 | 처리 | 내용 |
|---|---|---|---|
| F1 | 중요, 수정 | **수정**(실기기 미검증) | 트랙마다 녹음 스레드 1개가 COM 초기화(`com_init`, CoInitializeEx MTA) → 장치 열기 → 읽기 → 닫기 → CoUninitialize 를 모두 합니다. 근거(soundcard 0.4.6 `mediafoundation.py`): CoInitializeEx 는 모듈을 불러오는 스레드에서만 부름(116줄 `_com = _COMLibrary()`, 39~49줄), 장치 열기(411~419줄 `IMMDevice::Activate`)·읽기(685~711줄 `GetService`·`GetBuffer`·`GetNextPacketSize`)에는 스레드별 초기화가 없음. 장치 목록 읽기는 계속 임시 스레드(COM 초기화·해제 포함). 시험: 여는 스레드 = 읽는·닫는 스레드, 화면 스레드가 아님(`test_open_read_close_on_same_thread`) |
| F2 | 중요, 수정 | **수정**(실기기 미검증) | 읽기 오류 시 장치를 닫고 1초마다 다시 열기. 새 스트림 시작 위치까지 무음을 채워 시간축 유지(표본 주파수가 달라지면 리샘플). '(기본)' 장치를 고르면 2초마다 윈도우 기본 장치를 확인해 바뀌면 다시 연결. 화면: 빨간 띠 경고 + 창 제목 ⚠ + 알림음 1번, 다시 연결되면 사라짐, 중지 후 상태 줄에 다시 연결 횟수. 시험: 끊김→열기 실패→재연결 후 딸깍 위치 일치, 기본 장치 변경 따라가기, 고정 장치는 안 따라감, 화면 경고 표시·해제 |
| F3 | 중요, 수정 | **수정** | 출발 시각 t0 = 두 스트림 시작 시각 중 이른 쪽, 늦게 열린 트랙은 차이만큼 앞을 무음으로 채움(`sync_to`, `lead_in_frames` 기록). 두 장치를 동시에 열고(각자 스레드) 열자마자 읽기 시작해 대기 중 버퍼가 넘치지 않음. `verify_recorder.py` A: 수정 전 0.4초 어긋남 → **차이 0.000초**. 시험 `test_open_delay_difference_is_compensated`(0.4초 늦게 열림, 허용 0.05초) |
| F4 | 코드 문제 아님 | 미수정(지시대로) | — |
| F5 | 보통, 수정 | **수정** | 가짜 장치 무음 기본값을 실제 soundcard 처럼 0 반환으로(`gap_mode="zeros"`), 장치 멈춤은 `gap_mode="stall"` 로 따로 시험. 중지 때 가짜 장치의 기다리는 읽기를 바로 돌려보냄(`cancel`), 녹음 스레드가 스스로 장치를 닫음. 무음 채움 기준을 WASAPI 버퍼(0.5초)+0.25초=0.75초로 올려, 버퍼 안의 정상 지연 데이터를 무음으로 밀지 않게 함. `verify_recorder.py` B: **0.01초, 스레드 종료, close 호출됨**. 시험 `test_stop_during_stall_is_fast_and_cleans_up` |
| F6 | 보통, 기록 | **README 반영** | 차단이 파이썬 소켓 단계라는 한계와 '방화벽으로 막아도 동작' 을 README 앞부분에 적음. 덤으로 exe 에서 `ssl`·메일·FTP·xmlrpc·http.server·webbrowser 모듈을 뺌 |
| F7 | 보통, 수정 | **수정**(일부 미확인 유지) | spec 에서 cudnn64_9.dll 제외(ctranslate2.dll·_ext.pyd 의 가져오기 목록에 없음을 pefile 로 확인), setuptools·PyYAML 제외. 라이선스 표에 VC++ 런타임, OpenSSL(libcrypto), libffi·zlib·bzip2·xz, OpenBLAS·GCC 런타임, typing_extensions, packaging 추가. libiomp5md·VC++ 런타임 재배포 조건과 학습 데이터 영향은 '미확인' 으로 표시. 워크플로가 cudnn 이 다시 들어오면 경고 |
| F8 | 보통, 수정 | **수정** | README 1~4절: 깃허브 로그인 필요, 아티팩트 30일 뒤 사라짐, 사용자 폴더(`%USERPROFILE%\MeetingRecorder`)에 풀기(OneDrive 동기화 폴더 피하기), 한글 경로 경고 때 대처, 윈도우 마이크 개인정보 설정(새 3절). 화면에서도 마이크가 1분 넘게 조용하면 이 설정을 안내 |
| F9 | 경미, 수정 | **수정** | 워크플로: `$p.Handle` 을 잡은 뒤 종료 코드 읽기, 결과 파일 `ok` 가 true 이고 종료 코드가 0(또는 읽지 못해 비어 있음)일 때만 성공 |
| F10 | 경미, 수정 | **수정** | selftest 의 tkinter 확인은 exe 에서만 필수, 개발 환경에서는 참고 기록. tkinter 없는 가상환경(Python 3.13)에서 전체 시험 56 통과 + 화면 시험 2개 '실행 안 함' |
| F11 | 경미, 수정 | **수정** | 같은 사람 발언 합치기에 상한(60초, 600자). 넘으면 새 줄·새 시각. 시험 `test_long_monologue_is_split_into_lines` |
| F12 | 경미, 기록만 | 미수정(지시대로) | 디스크 쓰기 실패 뒤 그 트랙은 복구하지 않음(화면 경고는 뜸) |

### changes.md 정정 5건(본문에 '정정' 으로 표시)
1. "두 트랙 0.15초 안" → 장치 열기 시간 0 인 가짜 장치에서만 성립했음. F3 수정 후 0.4초 열기 지연에서 차이 0.000초.
2. "화면 없이 49 passed" → tkinter 설치 환경에서만 성립. F10 수정 후 tkinter 없는 환경도 통과.
3. CI → 계정 Actions 무료 시간 소진으로 한 번도 시작되지 못함(F4). 윈도우 빌드·selftest 증거 없음.
4. 미검증 목록에 COM 스레드(F1)·재연결(F2) 누락 → 추가(아래).
5. 2시간 메모리 → 측정이 아니라 30분 전체 측정 + 2시간 화자 분리 단독 측정을 더한 추정.

### 시험 결과(수정 1회차)
- Xvfb 화면 있음(Python 3.12, tkinter 있음): **58 passed**, 0 failed.
- 화면 없음(같은 환경): 56 passed, 2 '실행 안 함'(화면 시험).
- tkinter 없는 가상환경(Python 3.13): 56 passed, 2 '실행 안 함'(tkinter 없음).
- `audit/verify_recorder.py`: A 차이 0.000초, B 중지 0.01초·스레드 종료·close 호출.
- 리눅스 PyInstaller 재빌드(cudnn·ssl 등 제외 반영): `--selftest` 통과(메모리 최고치 990MB), `--fake-devices` 화면 6초 실행 오류 없음.

### 여전히 윈도우 실기기에서만 확인 가능한 것
- COM 초기화를 녹음 스레드로 옮긴 뒤 실제 WASAPI 열기·읽기·닫기가 정상인지(F1), 다른 스레드의 장치 목록 읽기와 함께 써도 되는지.
- 실제 장치 뽑기·블루투스 헤드셋 연결 끊김 때 soundcard 가 예외를 내는지(예외 없이 0 만 돌려주면 재연결이 일어나지 않음), 기본 장치 변경 감지(F2).
- 실제 장치의 스트림 시작 시각(Start 직후 `time.monotonic`)과 첫 표본 시각의 차이(장치 지연)가 두 장치 사이에서 얼마나 다른지(F3 보정은 열기 시간 차이만 맞춤).
- cudnn 을 뺀 묶음, ssl 을 뺀 묶음이 윈도우 exe 에서 동작하는지(CI selftest, F4 해결 후).
- 화면 경고(빨간 띠·알림음)의 윈도우 표시.
