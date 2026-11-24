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

## 1단계 CI 결과(총괄 확인)
- 실행 45306743702(커밋 a5e5f18)에서 `test_selftest_passes` 가 실패했습니다(selftest `ok: false`).
- 함께 나온 경고 `_COMLibrary.__del__ … AttributeError: '_COMLibrary' object has no attribute 'com_loaded'` 는 `__init__` 이 com_loaded 를 세우기 전에 예외를 냈다는 뜻이라 가설과 일치합니다.
- 실패 항목의 detail 은 로그 꼬리에서 잘려 보지 못했습니다. 이를 해결하려고 2단계에서 실패 요약 출력을 넣었습니다(아래 4).

## 2단계: 원인 수정과 화면

### 1. 원인 수정(`mrec/devices.py` `import_audio_module`)
- soundcard 는 'COM 을 아직 초기화하지 않은' 새 스레드에서 불러옵니다. 이전에는 `com_init()` 을 먼저 부르는 `_run_in_mta` 안에서 불러왔습니다.
- 그러면 soundcard 자신의 CoInitializeEx 가 S_OK 를 받아 불러오기가 성공합니다. 주 스레드(화면)에서는 여전히 불러오지 않습니다.
- 불러온 직후 그 스레드에서 soundcard 가 연 COM 을 닫고 `_com.com_loaded=False` 로 표시합니다. 프로그램 종료 때 주 스레드에서 짝 없는 CoUninitialize 가 불리지 않게 하려는 것입니다.
- 근거(불러온 스레드가 끝나도 다른 스레드에서 쓸 수 있는가): soundcard 는 모듈 단위로 COM 객체를 들고 있지 않습니다.
  - 모듈 수준 객체는 `_ffi`·`_ole32`·`_com` 뿐입니다.
  - 장치 객체는 id 문자열만 갖고(343~350줄 `_Device`), COM 객체는 부를 때마다 `_DeviceEnumerator`(209~236줄)로 새로 만듭니다.
  - 따라서 COM 을 초기화한 다른 스레드에서 목록 읽기와 장치 열기가 됩니다. 목록 읽기는 `_run_in_mta`, 열기·읽기는 녹음 스레드에서 하며, 녹음 스레드의 COM 초기화(F1)는 유지했습니다.
- 첫 불러오기가 실패한 뒤 다시 시도하는 경우:
  - 파이썬은 실행 중 예외가 난 모듈을 `sys.modules` 에서 지웁니다(`soundcard`, `soundcard.mediafoundation` 모두). 그래서 다시 부르면 처음부터 불러오고, `_ffi` 도 새로 만듭니다.
  - 시험 `test_import_audio_module_retries_cleanly_and_without_com_init` 이 이를 확인합니다. 첫 불러오기에서 `RuntimeError('Error 0x100000001')` 를 내는 가짜 패키지로 남은 모듈이 없음, 두 번째 성공, 불러오는 스레드에서 com_init 미호출을 봅니다.

### 2. 화면(`mrec/gui.py`)
- 장치 두 칸 맨 위에 **"자동(윈도우에서 쓰는 장치 그대로)"** 을 두고 기본값으로 했습니다.
  - 자동 = 녹음 스레드가 윈도우 기본 장치를 직접 열고, 바뀌면 따라갑니다.
  - 장치 목록을 못 읽거나 비어 있어도 자동으로 녹음을 시작할 수 있습니다.
- 목록의 장치를 고르면 그 장치로 고정합니다. '(현재 기본)' 장치를 골라도 고정되므로 감사 N3 이 해결됩니다.
- 장치 계층(backend)을 못 열면 숨기지 않습니다.
  - 장치 칸 아래에 빨간 원인 문구와 **[오류 내용 복사]** 를 보여 주고, [녹음 시작] 은 끕니다.
  - 복사 내용은 버전, OS, 파이썬, 문제 위치, 예외와 traceback 끝 10줄, 최근 로그 30줄입니다. 로그는 기술 정보만 담습니다.
- **[장치 새로고침]** 은 backend 가 없으면 만들기부터 다시 시도합니다. [녹음 시작] 도 backend 가 없으면 한 번 더 시도합니다.
- 목록 읽기 실패와 녹음 시작 실패도 같은 칸에 문구와 [오류 내용 복사] 로 보여 줍니다.
- 저장된 설정과 호환됩니다.
  - `mic_id`·`speaker_id` 가 없거나 None 이면 자동입니다. 예전에도 None 은 '기본 장치 따라가기' 였습니다.
  - 목록에 있는 id 면 그 장치, 목록에 없는 id 면 자동으로 둡니다. 자동은 None 으로 저장합니다.

### 3. N1(`mrec/recorder.py` `_abort_start`)
- 장치를 연 뒤 폴더·파일 준비가 실패하면, 녹음 스레드를 멈추고 장치를 닫은 뒤 예외를 다시 던집니다.
- 장치 열기 실패 경로도 같은 정리 함수를 씁니다.
- 시험: `test_start_failure_after_open_stops_threads`.

### 4. 자체 시험·CI 로그
- selftest 결과 JSON 맨 끝에 `failed_summary`(실패 항목 '이름: detail 앞 400자')를 둡니다. 콘솔이 있으면 마지막에 "=== selftest 실패 항목 ===" 으로 한 번 더 출력합니다.
- 워크플로의 exe selftest 단계는 이 목록을 `::error::` 로 출력합니다.
- pytest 의 `test_selftest_passes` 는 실패 시 assert 메시지에 `failed_summary` 를 담습니다(pytest 마지막 요약 줄에 보임).
- 화면 경로 검사는 이미 Tk 루트가 있으면(시험 프로세스) 그것을 씁니다. Tk 는 프로세스당 하나만 만듭니다.

### 5. README
- '5. 첫 시험' 의 장치 안내를 새 화면(자동 기본, 고정은 선택, 오류 내용 복사)에 맞게 고쳤습니다.

### 검증
- pytest(Xvfb): **68 passed**.
- 화면 없음: 63 passed, 5 '실행 안 함'. tkinter 없음: 63 passed, 5 '실행 안 함'.
- 리눅스 PyInstaller 재빌드: `--selftest` 통과(`failed_summary: []`, JSON 마지막 키). `--fake-devices` 화면 6초 정상.
- 장치 계층이 실패하는 실제 경로(리눅스 exe 에서 soundcard 불러오기 실패)에서 화면이 멈추지 않고 로그에 원인이 남는 것을 확인했습니다.
- 화면 캡처로 오류 문구와 [오류 내용 복사] 배치를 확인했습니다.

### 확인 못 한 것(실기기·윈도우 CI 필요)
- 윈도우 CI 에서 '화면 경로' 검사 통과(수정 확인). CI 러너에는 소리 장치가 없어 목록이 0개일 수 있고, 그 경우 목록·기본 장치 조회는 참고로만 판정됩니다.
- 사용자 PC 에서 목록이 채워지는지, 자동으로 실제 녹음되는지, 기본 장치 변경 따라가기.
- `_release_import_thread_com` 이 soundcard 의 비공개 속성(`_com.com_loaded`)을 쓰므로 soundcard 버전을 고정(0.4.6)해야 합니다. 다른 버전에서는 조용히 건너뜁니다.
- N2(재연결 실패가 계속될 때 1초마다 recorder 생성 3번, COM 객체 해제)는 이번 범위 밖이라 그대로입니다.
