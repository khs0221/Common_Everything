# R5 changes — 특정 마이크(USB 헤드셋)를 열지 못하는 문제

작성: engineer. 시작 커밋 a6ef3f4(0302bd3 + R5 계획서). 실기기 재현은 할 수 없습니다.
아래에서 **[소스]** = soundcard 0.4.6 소스로 확인, **[추정]** = 소스와 증상으로 추정, **[미검증]** = 윈도우 실기기에서만 확인 가능.

## 1. soundcard 0.4.6 에서 '문자열이 빈 예외' 가 나올 수 있는 지점(recorder() → __enter__ 경로)

`check_error` 는 HRESULT 가 0 이 아니면 항상 `'Error 0x…'` 나 설명 문장을 붙여 예외를 냅니다(78~105줄) **[소스]**. 따라서 빈 메시지는 assert 같은 파이썬 단계에서만 나옵니다.

| 위치(mediafoundation.py) | 내용 | 빈 메시지 | 우리 대응 |
|---|---|---|---|
| 516 `assert Format.wFormatTag == 0xFFFE` | 장치 믹스 형식이 WAVEFORMATEXTENSIBLE 가 아니면(예: 일반 WAVEFORMATEX, 태그 1·3) 실패 | 예 | 'direct' 열기: 믹스 형식을 쓰지 않고 표준 배치의 float 형식을 직접 만들어 Initialize |
| 517 `assert Format.cbSize == 22` | 확장 정보가 없는 형식(cbSize 0)이면 실패 | 예 | 같음 |
| 522~525 SubFormat Data1~Data4 assert | **[소스]** cffi 정의가 정렬(pack 1)을 안 해 SubFormat 을 실제(24바이트)보다 4바이트 뒤(28바이트)에서 읽습니다. 그래서 실제로는 표준 KSDATAFORMAT GUID 의 공통 꼬리만 검사하며, PCM·float 둘 다 통과합니다. 소스 주석의 "경험으로 찾은 값" 이 이 때문입니다(시험 `test_soundcard_cffi_layout_reads_subformat_4_bytes_late`) | 예(비표준 GUID 일 때만) | 같음 |
| 406 `assert blob.cbSize == 40`(`channels` 속성) | 장치 형식 속성이 40바이트가 아니면 실패. `recorder(channels=None)` 이나 `repr(mic)` 에서 불림 | 예 | 기존 조합은 channels=1 이라 지나지 않음. '장치값 채널' 조합은 맨 끝에만 두고, 진단에서는 따로 잡아 '확인 실패' 로 적음 |
| 499~506 채널 맵 TypeError, 375·401 RuntimeError, 186 IndexError | 메시지 있음 | 아니오 | 진단에 그대로 남음 |
| cffi·numpy(`_ffi.new`, `frombuffer`) | 메모리 부족 등. 열기 단계에서는 가능성 낮음 | 경우에 따라 | 진단에 종류·위치 남김 |

- **가장 유력한 원인 [추정]**: USB 헤드셋 마이크의 공유 모드 믹스 형식이 WAVEFORMATEXTENSIBLE 가 아니라서(516 또는 517줄) 문자열 없는 AssertionError 가 났을 가능성이 높습니다.
  - 세 주파수가 모두 같은 지점에서 실패한 것과 맞습니다. 다채널 내장 마이크는 확장 형식이라 통과합니다.
  - 확인은 [미검증] 입니다. 다음 [오류 내용 복사] 에 `AssertionError() @ mediafoundation.py:516 __init__` 같은 위치와 장치의 실제 믹스 형식(태그·cbSize)이 함께 나오므로 한 번에 확정할 수 있습니다.
- 함께 발견한 것 [소스]: soundcard 경로는 믹스 형식 버퍼를 고쳐 쓰는데, 정렬 차이로 `Samples`(유효 비트) 대신 채널 마스크 하위 2바이트를 덮어쓰고 SubFormat 은 바꾸지 않습니다. 그래서 믹스 형식이 PCM 확장 형식인 장치에서는 '32비트 PCM' 으로 열려 float 로 읽힐 위험이 있습니다(소리가 깨짐, 오류는 아님) [추정].
  - 지금 되는 장치를 바꾸지 않으려고 순서는 그대로 두었습니다. 대신 진단에 믹스 형식(subformat)을 남깁니다.

## 2. 열기 조합(순서) — `devices.OPEN_COMBOS`

| 순서 | 방법 | 주파수 | 채널 | WASAPI 버퍼 |
|---|---|---|---|---|
| 1~3 | soundcard(R4 까지와 같음) | 16000 → 48000 → 44100 | 1 | 0.5초 |
| 4~5 | direct(표준 40바이트 float 형식 직접 Initialize, 같은 플래그) | 16000 → 48000 | 1 | 0.5초 |
| 6 | direct | 장치 믹스 주파수 | 장치 믹스 채널(읽은 뒤 모노로 합침) | 0.5초 |
| 7 | soundcard | 48000 | 장치값(soundcard 기본) | soundcard 기본 |

- 1번이 열리는 장치(지금의 내장 마이크·루프백)는 이전과 똑같은 호출로 열립니다(시험 `test_working_device_opens_with_first_combo_unchanged`). 시각 맞춤(F3)·리샘플러 경로는 그대로입니다.
- 16kHz 가 아닌 조합으로 열리면 기존처럼 녹음 파일이 그 주파수로 쓰이고, 처리 때 16kHz 로 바뀝니다.
- direct 는 soundcard 의 `_Recorder` 를 그대로 쓰고 초기화만 대신합니다(`make_direct_recorder`).
  - 형식 바이트: `pack_float_format` = 태그 0xFFFE, 32비트, cbSize 22, 유효 비트 32, 마스크(모노 0x4·스테레오 0x3), SubFormat IEEE_FLOAT.
  - 플래그: AUTOCONVERTPCM|SRC_DEFAULT_QUALITY|NOPERSIST|0x00100000, 루프백이면 LOOPBACK 추가.
  - 윈도우에서의 동작은 [미검증] 입니다.
- 어떤 조합으로 열렸는지 녹음 시작 로그('연 방법')와 [장치 점검] 결과에 남깁니다.

## 3. 진단 보강
- 모든 조합이 실패하면 `DeviceOpenError` 를 냅니다(`raise … from` 마지막 예외).
  - 메시지에는 조합마다 '방법/주파수/채널/버퍼: 예외 종류, repr, HRESULT(있으면), 마지막 위치(파일:줄 함수)' 를 한 줄씩 적습니다.
  - 장치 속성도 붙입니다: 이름, 루프백 여부, soundcard 채널 수(또는 '확인 실패 AssertionError mediafoundation.py:406'), 실제 믹스 형식(GetMixFormat 의 바이트를 표준 배치로 읽은 태그·채널·주파수·비트·cbSize·유효 비트·채널 마스크·SubFormat GUID).
- 이 내용은 화면 오류 칸, [오류 내용 복사], 로그(`log.exception`) 에 그대로 들어갑니다.
- 일부 조합만 실패하고 열린 경우에도 앞서 실패한 조합 목록이 [장치 점검]·자체 시험 결과에 남습니다.

## 4. [장치 점검]·자체 시험
- 화면 '1. 장치' 에 **[장치 점검]** 을 추가했습니다.
  - 녹음하지 않고 고른 마이크와 스피커(루프백)를 1초씩 엽니다. 녹음 스레드와 같은 규칙으로, COM 을 초기화한 새 스레드에서 합니다.
  - 열리면 "열림 — 장치 · 방법 · 소리 크기(dBFS)" 를 보여 줍니다. 못 열면 3절 진단을 오류 칸과 [오류 내용 복사] 에 그대로 넣습니다.
  - 화면이 멈추지 않게 별도 스레드에서 돌립니다. 녹음 중에는 하지 않습니다.
- `--selftest`(윈도우): 화면 경로 검사 뒤에 **실제 장치 열기** 를 합니다.
  - 목록의 마이크(최대 8개)와 기본 스피커 루프백을 0.2초씩 열어 조합·주파수·크기를 적습니다. 못 열면 실패로 판정하고 detail 에 조합별 예외를 남깁니다.
  - 장치 0개(CI)는 참고입니다. 사용자 PC 에서 `MeetingRecorder.exe --selftest` 를 돌리면 원인 자료가 됩니다.

## 5. 이월 경미 건
- E1: UTF-16 용어 파일(BOM 있음·없음, 리틀·빅 엔디언)을 읽습니다.
  - 어떤 인코딩으로도 못 읽으면 화면에 "hint_terms.txt 읽지 못함(메모장에서 'UTF-8'로 다시 저장…)" 으로 표시합니다. 예전에는 '용어 파일 없음' 으로 보였습니다.
- E2: README 내려받기 크기 정정(남아 있던 600MB → 약 2GB).
  - 처리 품질별로 필요한 모델만 확인합니다(`models.missing_for`). 정확 모델만 없을 때도 '빠름' 은 쓸 수 있고, 시작할 때 쓸 수 있는 품질을 안내합니다.

## 시험
- pytest(Xvfb): **113 passed**(+ 장치 점검 화면 시험 포함). 화면 없음: 106 passed, 8 '실행 안 함'. tkinter 없음: 106 passed, 8 '실행 안 함'.
- 새 시험 `test_device_open.py`:
  - 되던 장치는 첫 조합 그대로
  - 문자열 없는 AssertionError 를 내는 장치 흉내 → direct 로 열림, 실패 위치 기록
  - 전부 실패 → 조합별 진단·장치 속성·원인 연결
  - 모노 요청만 실패 → 장치 채널로 열고 모노로 합침
  - 형식 바이트 40바이트
  - soundcard cffi 배치 4바이트 어긋남 재현
  - 장치 점검 성공·실패
- 그 밖: `test_selftest_device_path.py`(실제 장치 열기 항목), `test_gui.py`([장치 점검]), `test_glossary.py`(UTF-16, 읽지 못함, 품질별 모델 확인).
- 리눅스 묶음 `--selftest` 통과(55.1초, 메모리 최고치 2,646MB). 윈도우 전용 경로는 리눅스에서 '참고' 로 건너뜁니다.

## 실기기에서 확인할 것
1. USB 헤드셋 마이크를 고르고 [장치 점검] → "열림" 과 방법(예상: direct/16000Hz/1ch, 앞서 실패 3개)과 소리 크기.
2. 실패하면 [오류 내용 복사] 의 조합별 '@ mediafoundation.py:줄' 과 '믹스 형식'(tag·cbSize·subformat)을 총괄에게 보내기.
3. 녹음 → 처리 결과에서 내 목소리가 정상(깨지지 않음)인지.
4. 내장 마이크·루프백은 이전처럼 'soundcard/16000Hz/1ch' 로 열리는지(로그 '연 방법').
5. `MeetingRecorder.exe --selftest` 결과의 '실제 장치 열기' 항목.
