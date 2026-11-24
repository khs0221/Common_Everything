# R2 changes — 실기기에서 장치를 못 읽는 문제

작성: engineer. 시작 커밋 8011985(82aa2e2 + R2 계획서).

## 1단계: 재현 장치(원인 수정 전)

### soundcard 0.4.6 소스 확인(계획서 2절 가설)
- 결론: **가설이 소스와 맞습니다.** 같은 스레드에서 COM 을 먼저 초기화한 뒤 `import soundcard` 를 하면, 불러오기가 RuntimeError 로 실패합니다.
- 근거(`soundcard/mediafoundation.py`):
  - 39~49줄 `_COMLibrary.__init__` 이 `CoInitializeEx(NULL, COINIT_MULTITHREADED)` 를 부르고, 반환값을 `check_error(hr)` 로 검사합니다.
  - 78~105줄 `check_error` 는 `hresult == S_OK(0)` 일 때만 통과합니다. S_FALSE(1, '이 스레드는 이미 초기화됨')는 마지막 else 로 가서 `RuntimeError('Error 0x100000001')` 를 냅니다.
  - 57~67줄 예외 처리는 RPC_E_CHANGED_MODE(0x80010106)만 무시하고 나머지는 다시 던집니다.
  - 116줄 `_com = _COMLibrary()` 가 모듈을 불러올 때 실행되므로, 모듈 불러오기 자체가 실패합니다.
- 우리 코드에서 이 순서가 생기는 곳: `devices._run_in_mta.work()` 가 먼저 `com_init()`(CoInitializeEx → S_OK)을 부르고, 같은 스레드에서 `SoundcardBackend.__init__` 의 `__import__("soundcard")` 를 실행합니다.
  그러면 soundcard 의 두 번째 CoInitializeEx 가 S_FALSE 를 받아 예외가 납니다. 이 경로는 R1 수정 1회차(F1, `com_init` 추가)에서 생겼습니다.
- R1 CI selftest 가 통과한 이유: selftest 는 주 스레드에서 COM 초기화 없이 바로 `import soundcard` 를 해서 이 경로를 지나지 않았습니다.
- 확인 방법: 소스 읽기만 했습니다. 윈도우 실행으로는 아직 확인하지 않았으며, 아래 자체 시험으로 CI 에서 재현할 예정입니다.

### 넣은 자체 시험(`--selftest`, 윈도우에서만)
`mrec/selftest.py` 의 `gui_device_path`. 이 프로세스에서 soundcard 를 처음 불러오는 조건을 지키려고 다른 soundcard 검사보다 먼저 실행합니다.

| 검사 이름 | 판정 |
|---|---|
| 화면 경로: Tk 루트 생성 | 실패 시 실패 |
| 화면 경로: 장치 계층 열기(default_backend) | 불러오기·초기화 실패 시 실패. detail 에 '검사 전 soundcard 불러옴' 여부를 적음 |
| 화면 경로: 마이크 목록 읽기 / 스피커 목록 읽기 | 예외면 실패, 0개면 참고(통과) |
| 화면 경로: 기본 마이크 id / 기본 스피커 id(녹음 스레드 방식) | 녹음 스레드처럼 COM 초기화한 스레드에서 조회. 예외면 실패, 단 그 종류 장치가 0개면 참고 |

- 실패 detail 에는 예외 종류·메시지·traceback 마지막 8줄만 남깁니다(회의 내용 없음).
- 리눅스에서는 '윈도우 전용이라 건너뜀'(참고)으로 적습니다.
- 판정 로직 시험은 `tests/test_selftest_device_path.py` 입니다(가짜 backend 로 실패·성공·장치 0개).
- 예상: 수정 전 코드에서는 윈도우 CI 의 '장치 계층 열기' 가 `RuntimeError: Error 0x100000001` 로 실패해야 합니다.

### 시험
- Xvfb: 62 passed.
- 새 시험 파일만 따로(Python 3.12 / tkinter 없는 3.13): 3 passed / 3 passed.
