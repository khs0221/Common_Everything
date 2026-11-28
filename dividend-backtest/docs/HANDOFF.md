# 배당성장주 시스템 종합 정리 & 다음 세션 인수인계 (`HANDOFF.md`)

> **최종 업데이트:** 2026-11-28 (KST)  
> **브랜치 / PR:** `Common_Everything` → `feat/dividend-backtest` (**PR #10 OPEN**)  
> **보안 원칙:** 본 문서는 공개 저장소용 인수인계 문서입니다. 개인 계좌 금액·보유 수량·명의·시트 ID·API 키는 일절 기록하지 않습니다.

---

## 1. 한눈에 보는 완료 현황 (배당성장주 검토·운용 체계 완성도)

현재 배당성장주 투자에 필요한 **① 자동 모니터링·정기 판정**, **② 백테스트·과적합 검증**, **③ 실전 의사결정 5종 리포트(스크리닝·정성감사·우선주·현금흐름/절세·스트레스 테스트)**가 모두 구축 완료되었습니다.

| 영역 | 담당 저장소 | 실행 방식 | 상태 |
|---|---|---|---|
| **일상 공시 감시 · 실적 리포트 · 정기 판정 · 리밸런싱** | 비공개 운영 트래커 (`dividnd-stock-tracker`) | GitHub Actions + `cron-job.org` **완전 자동** (텔레그램 + Google 시트) | ✅ 구축 완료 *(11/24~ 월 2,000분 한도 리셋 확인 필요)* |
| **월간 전 종목 스크리너 (11p PDF)** | 비공개 운영 트래커 (`monthly-screener.yml`) | 매월 1일 10:00 KST **자동** (텔레그램 PDF) | ✅ 구축 완료 |
| **전략 백테스트 · 블라인드 OOS · PIT DB 연구실** | `Common_Everything/dividend-backtest` | Google Colab 수동 실행 (연구·검증용) | ✅ 6개 노트북 완료 |
| **실전 의사결정 보조 리포트 4종 (5p A4 PDF)** | `Common_Everything/dividend-backtest` | Google Colab 수동 실행 → Drive 자동 저장 | ✅ 4개 노트북 완료 & PDF 검증 완료 |

---

## 2. 두 저장소별 검토 항목 & 리포트 카탈로그

### A. 기존 비공개 트래커 (`dividnd-stock-tracker`) — "가만히 있어도 자동으로 오는 것"

| 주기 · 시각 (KST) | 워크플로 / 모듈 | 검토 내용 | 받는 리포트 / 채널 |
|---|---|---|---|
| **화~금 09:15** | `track.yml` (`daily`, `earnings-auto`) | 전날 주요 공시(배당·자사주·소각·유/무상증자·합병·분할·감자·최대주주·밸류업·실적·주주환원 공정공시), 현금배당 기준일/지급일 파싱, 고배당기업(분리과세) 여부, 약속표 갱신 감지, 매도 검토 72시간 재대조 | 텔레그램 알림 + 새 실적 공시 시 **실적 추적 HTML 리포트(+IR 발췌)** 자동 첨부 |
| **월 09:15** | `track.yml` (`weekly`) | 최근 7일 공시, 주가·52주 낙폭, 시트 연동 포트폴리오 손익, 임원·5% 큰손 장내매매 변동, 금주·차주 확정 일정 | 텔레그램 주간 요약 + 주말 실적 공시 시 HTML 리포트 |
| **매월 1일 09:15** | `track.yml` (`전체 판정`, `rebalance`, `dashboard`, `calendar`) | 1~9번 정량 기준 재계산, 품질 종합·상태 등급·경고 신호 ①~⑧, 재투자 순위, A/B/C 바구니 비중·상한 점검, 향후 3개월 일정 | 텔레그램(요약 + 종목별 그래프 HTML + 리밸런싱 HTML + 일정 달력) + Google 시트(`판정`·`판정 이력`) + `reports/해설/` 커밋 |
| **매월 1일 10:00** | `monthly-screener.yml` (`run_screener.py`) | 코스피·코스닥 시총 3,000억+ 전수 스캔, 생존 DNA/알파 순위, 절대 밸류 캡(PER/PBR)·저변동성 점검 | 텔레그램 요약 + **11페이지 A4 PDF** + Actions 아티팩트(30일) |
| **6·9·12월 1일** | `track.yml` (`quarterly --auto`) | 보유 종목 공통 지표 나란히 비교, 종목별 실적 방향 요약, 동종 업종 피어 비교, 확인 필요 종목 추출 | 텔레그램 **분기 점검 HTML** + Google 시트 `결정 기록` 탭 자동 추가 |
| **4월 1일** | `track.yml` (`annual`) | 분기 점검 + 전 종목 후보 탐색 요약 + 신규 편입 후보 TOP 5 + 지난 1년 회고 *(전 종목 후보 탐색은 1.5시간 소요로 수동 실행 후 재실행)* | 텔레그램 **연간 점검 HTML** |
| **매일 18:52 예약** | `watchdog.yml` | 일간·주간·월간 실행 누락/실패 감시, 토큰 만료·연간 할 일 리마인드, 월요일 정상 생존 신호 | 텔레그램 경보 / 생존 신호 |
| **수동 버튼(요청형)** | `track.yml` (`workflow_dispatch`) | 임의 종목 검토, 매도 검토(72시간 쿨다운 포함), 특정 분기 실적 추적, 전 종목 후보 탐색, 최근 30/90일 공시, 백지 점검(`blind-review.yml`) | 텔레그램 + 저장소 리포트 |

---

### B. 이번 `Common_Everything/dividend-backtest` — "필요할 때 Colab에서 돌리는 연구 + 고가독성 5p PDF 도구"

#### 1) 실전 의사결정 & 리포트 5종 (운영 보조 핵심)

| # | 노트북 | API 키 | 검토 항목 | 생성 리포트 (Drive 자동 저장) | 권장 주기 |
|---|---|---|---|---|---|
| **1번** | `colab_dividend_cashflow_sim.ipynb` | **불필요** | 10년 배당 현금흐름(Yahoo 10년 실측 변동성/상관 몬테카를로 5,000회), 배분안별 10년 자산/월배당 비교, 월 적립금별 목표 월배당 도달 연도, ISA·연금 10년 절세액, 금융소득종합과세(2,000만) 경보 | **5페이지 A4 가로 PDF** + HTML + CSV (`MyDrive/Colab Notebooks/dividend_cashflow_sim/reports/`) | 반기 1회 · 자산/적립금 변경 시 |
| **2번** | `colab_portfolio_stress_test.ipynb` | **불필요** | 6대 역사적·가상 위기(2008·2020·2022·AI -50%·금융규제·원화강세) 방어력 & 배당 유지율, 10년 주간 실측 상관관계 히트맵(520주), -35% 폭락장 원금 회복 & 리밸런싱 비교, 3단계 행동 매뉴얼 | **5페이지 A4 가로 PDF** + HTML + CSV (`MyDrive/Colab Notebooks/dividend_stress_test/reports/`) | 반기 1회 · 비중 큰 변동 시 |
| **3번** | `colab_preferred_tracker.ipynb` | **불필요** | 국내 대표 우선주 vs 보통주 10년 총수익·괴리율 현재 백분위(10년 기준), 보유 우선주 스포트라이트 카드, 보통주 vs 우선주 신규 매수 판독 | **5페이지 A4 가로 PDF** + HTML + CSV (`MyDrive/Colab Notebooks/preferred_tracker/reports/`) | 분기 1회 · 우선주 매수 전 |
| **4번** | `colab_screener.ipynb` | 무료 `DART` | 매년 4~5월 코스피·코스닥 전 종목 스크리닝, 보유주 검진, 신규 매수 배분 가이드 *(개인 시트/보유는 Colab 로컬/Drive 비공개 연동)* | **A4 PDF** + HTML + CSV | 연 1회 (4~5월 사업보고서 확정 후) |
| **5번** | `colab_qualitative_ai_monitor.ipynb` | 무료 `DART` (+선택 `GEMINI`) | OpenDART 공시 원문 + Drive IR PDF 수집 → 신호 ⑤(배당 삭감 사유) · ⑥(주주환원 정책 변화) · ⑦(자본배분·유증·CB 리스크) 정성 감사 *(Gemini HTTP 402 시 자동 우회 + 원문 번들 `.md`/`.json.gz` Drive 저장)* | **A4 PDF** + HTML + 원문 번들(`qualitative_raw_bundle_latest.md`) (`MyDrive/Colab Notebooks/qualitative_monitor/reports/`) | 분기 1회 · 텔레그램에 대형 공시 떴을 때만 |

#### 2) 전략 검증 & 백테스트 연구실 5종 (알고리즘 설계·검증 완료)

| 노트북 | API 키 | 역할 및 검증 완료 내용 |
|---|---|---|
| `colab_backtest.ipynb` | 불필요 | 기본 전략 A vs 보완 전략 B 과거 백테스트(2019-04~), 장기보유 매도규칙, 5개 필터 Ablation |
| `colab_alpha_lab.ipynb` | 불필요 | 알파 팩터 연구실 · Walk-Forward · IC · 몬테카를로 500회 · PIT 시총 필터 과적합 차단 4중 검증 |
| `colab_pit_builder.ipynb` | 무료 `DART` | 코스피·코스닥 전 보통주(~2,300개사) 풀 유니버스 PIT DB 빌더 (Drive 이어받기 지원) |
| `colab_final_portfolio_backtest.ipynb` | 불필요 | 버그 4종 보정 + 진성 주주환원 + 상대 70% 변동성 게이트 + 금융 ≤50% + A+B+C 적립식 통합 백테스트 |
| `colab_blind_oos_lab.ipynb` | 불필요 | 진성 블라인드 OOS 검증실 (2017~2022 탐색 동결 → 2022~2026 봉인 구간 단독 채점 · 사후편향 측정) |

---

## 3. 이번 세션에서 해결한 핵심 기술 이슈 (재발 방지 메모)

1. **Yahoo Finance `KRW=X` 이상치 데이터 버그 (해결 완료):**
   - `2017-04-30` 주간/월간 시세에 `0.114077`(정상값 `1140.77`의 1/10,000)이 섞여 있어 환율 상관관계가 `+1.00`으로 왜곡되던 문제를 발견.
   - `colab_dividend_cashflow_sim.ipynb`와 `colab_portfolio_stress_test.ipynb` 모두 `KRW=X` 수집 직후 `(s >= 800.0) & (s <= 2000.0)` 정상 범위 필터를 적용해 실측 상관관계(국내 주식 vs 환율 `-0.14 ~ -0.29`)를 정상화함.
2. **Colab Drive 마운트 실패 (`ValueError: mount failed`) 대응 (해결 완료):**
   - 1번·2번·3번·5번 노트북 모두 `drive.mount(..., force_remount=True)` 자동 재시도 + 실패 시 `google.colab.files.download` 브라우저 즉시 다운로드 폴백을 내장.
3. **Google AI Studio 선불 크레딧 `HTTP 402` 잠금 우회 (해결 완료):**
   - Prepay 빌링이 활성화된 계정에서 잔액 `$0` 상태일 때 모든 무료 키가 `HTTP 402`로 차단되는 현상 확인.
   - 5번 노트북에 `HTTP 402` 서킷 브레이커 + 전체 공시·IR 원문을 하나의 번들(`qualitative_raw_bundle_latest.md` / `.json.gz`)로 Drive에 자동 저장하는 기능을 추가해, API 과금 없이도 AI 채팅 세션에 번들을 넘겨 정성 감사 PDF를 만들 수 있게 설계함.
4. **PDF 가독성 & 차트 시인성 표준화 (해결 완료):**
   - 1번·2번·3번 모두 **5페이지 A4 가로 레이아웃**(1p 핵심 KPI·30초 가이드·요약표 / 2p 실전 가이드·매뉴얼 / 3~5p 페이지당 전체 폭 차트 1개 + 그래프 읽는 법 박스)으로 통일하고, WeasyPrint + NanumGothic 렌더링 후 전 페이지 시각 검수를 마침.

---

## 4. 다음 세션 / 사용자 후속 체크리스트 (할 일)

- [ ] **1. `Common_Everything` PR #10 머지 & 배지 브랜치 정리:**
  - 현재 모든 신규 노트북과 문서는 `feat/dividend-backtest` 브랜치(PR #10)에 올라가 있습니다.
  - PR #10을 `main`으로 머지한 뒤, `dividend-backtest/README.md` 상단 Open-in-Colab 배지 URL의 `feat/dividend-backtest`를 `main`으로 일괄 치환하세요.
- [ ] **2. 비공개 트래커(`dividnd-stock-tracker`) GitHub Actions 정상 복구 확인:**
  - 2026-11-24부터 월 무료 2,000분 소진으로 추정되는 즉시 실패(2~5초, 로그 없음)가 발생 중입니다.
  - GitHub `Settings → Billing and plans → Plans and usage`에서 Actions 무료 시간 리셋일을 확인하고, 리셋 후 **12월 1일 `전체 판정` · `monthly-screener` 텔레그램 수신**이 정상 동작하는지 확인하세요.
- [ ] **3. (선택) 평상시 운영 루틴 유지:**
  - 매일·매주·매월 자동 알림은 기존 비공개 트래커 텔레그램으로 수신.
  - 분기 1회 또는 신규 매수/리밸런싱 시점에만 본 저장소의 **1번(현금흐름)·2번(스트레스)·3번(우선주)·5번(정성 번들)** Colab을 필요에 따라 선택 실행.
- [ ] **4. (선택) 5번 노트북 AI 완전 자동화가 필요할 때:**
  - 현재는 `원문 번들 저장 → AI 세션 전달` 무료 방식으로 충분합니다. 추후 Colab 원클릭으로 AI 텍스트까지 자동 생성하고 싶다면 Google AI Studio 선불 `$5` 충전 또는 무료 티어 LLM(예: Groq / OpenRouter 등) 엔드포인트 옵션 추가를 검토하세요.

---

## 5. 다음 에이전트 세션을 위한 작업 수칙

1. **저장소 경계 엄수:** `khs0221/dividnd-stock-tracker`는 사용자의 명시적 승인 없이 절대 수정·커밋하지 마세요 (읽기 전용 참조만 허용).
2. **공개 저장소 프라이버시 엄수:** `khs0221/Common_Everything`은 **Public** 저장소입니다. 개인 계좌 평가액, 보유 수량, 가족 명의, 비공개 Google 시트 ID, 개인 맞춤 PDF는 절대 커밋하지 말고 비공개 Google Drive 또는 로컬 `/tmp`에서만 다루세요.
3. **시각 검증 원칙:** PDF 리포트나 차트를 수정할 때는 반드시 WeasyPrint로 렌더링한 PDF를 직접 이미지로 열람해 텍스트 겹침·잘림·기호 오류(`+-` 등)가 없는지 전 페이지 확인 후 완료 보고하세요.
4. **Google Drive CLI 참고:** `gdrive mutate update <FILE_ID> <LOCAL_PATH>` (위치 인자), `gdrive mutate upload <LOCAL_PATH> --parent <FOLDER_ID>`.
