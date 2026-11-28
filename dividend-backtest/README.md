# 한국 배당성장주 백테스트 연구소 (`dividend-backtest`)

Google Colab에서 한국 배당성장주 전략을 **백테스트(장기보유 매도규칙 · Ablation 포함)**하고, **매년 4~5월 코스피·코스닥 전 종목 스크리닝 · 내 보유주 정기 검진 · 매수 배분 가이드 · A4 PDF 리포트 누적 저장**까지 수행하는 독립 실행 프로젝트입니다.

| 노트북 | 목적 | API 키 | Colab 바로 열기 |
|---|---|---|---|
| `notebooks/colab_backtest.ipynb` | **과거 백테스트(2019-04~) + 장기보유 매도규칙 + 5개 필터 분해 + PDF 리포트** | **불필요** | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/khs0221/Common_Everything/blob/feat/dividend-backtest/dividend-backtest/notebooks/colab_backtest.ipynb) |
| `notebooks/colab_screener.ipynb` | **매년 4~5월 전 종목 스크리너 · 보유주 정기 검진 · 매수 배분 + PDF 리포트** *(개인 보유/시트는 비공개 전용)* | **무료 `DART_API_KEY`** | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/khs0221/Common_Everything/blob/feat/dividend-backtest/dividend-backtest/notebooks/colab_screener.ipynb) |
| `notebooks/colab_alpha_lab.ipynb` | **알파 팩터 연구실 · 과적합 차단 4중 검증(Walk-Forward · IC · 몬테카를로 500회 · PIT 시총 필터)** | **불필요** | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/khs0221/Common_Everything/blob/feat/dividend-backtest/dividend-backtest/notebooks/colab_alpha_lab.ipynb) |
| `notebooks/colab_pit_builder.ipynb` | **전 종목(~2,300개사) 풀 유니버스 PIT DB 빌더** *(Drive 자동 저장 · 2일 이어받기 -> `colab_alpha_lab` 자동 연동)* | **무료 `DART_API_KEY`** | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/khs0221/Common_Everything/blob/feat/dividend-backtest/dividend-backtest/notebooks/colab_pit_builder.ipynb) |
| `notebooks/colab_final_portfolio_backtest.ipynb` | **최종 통합 백테스트 (버그 4종 보정 · 진성주주환원 · 상대70% 게이트 · 금융≤50% · A+B+C 적립식 · 9.7년 PIT + 14.7년 추정)** | **불필요** | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/khs0221/Common_Everything/blob/feat/dividend-backtest/dividend-backtest/notebooks/colab_final_portfolio_backtest.ipynb) |
| `notebooks/colab_blind_oos_lab.ipynb` | **진성 블라인드(백지상태) OOS 검증실 (탐색 17~22 동결 -> 봉인 22~26 단독 채점 · 사후편향 거품 측정 · 종목수/리밸월 스트레스)** | **불필요** | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/khs0221/Common_Everything/blob/feat/dividend-backtest/dividend-backtest/notebooks/colab_blind_oos_lab.ipynb) |
| `notebooks/colab_qualitative_ai_monitor.ipynb` | **OpenDART 공시 원문 + Drive IR PDF × Gemini AI 정성 리스크 감시기 (신호 ⑤ 배당사유 · ⑥ 환원정책 · ⑦ 자본배분)** | **무료 `DART` + `GEMINI`** | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/khs0221/Common_Everything/blob/feat/dividend-backtest/dividend-backtest/notebooks/colab_qualitative_ai_monitor.ipynb) |
| `notebooks/colab_preferred_tracker.ipynb` | **우선주 vs 보통주 10년 총수익 · 괴리율 백분위 · 신규 매수 판독기 (보유 우선주 자동 태그 · 재검토선 감시)** | **불필요** | [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/khs0221/Common_Everything/blob/feat/dividend-backtest/dividend-backtest/notebooks/colab_preferred_tracker.ipynb) |

> *(참고: PR이 기본 브랜치로 머지된 뒤에는 위 Colab 배지 링크의 브랜치명만 기본 브랜치로 바꾸면 됩니다.)*

---

## 프로젝트 구성

```text
dividend-backtest/
├── README.md                                  # 프로젝트 안내 및 실행 방법
├── docs/
│   └── analysis_report.md                     # 1~3단계 전략 검증 · 데이터 진단 · Colab 설계 리포트
├── data/
│   ├── kr_fundamentals_pit.jsonl.gz           # 한국 상장사 374개사 공개 DART 시점고정 재무·배당 (FY2018~2024 + FY2025 실제 DPS, ~686KB)
│   ├── kr_treasury_10y_annual.csv             # 한국 국고채 10년물 연평균 금리 (2013~2025, 한국은행 ECOS)
│   └── kospi_snapshots.json                   # KOSPI 연말·3월말 스냅샷 (오프라인 폴백 참고용)
└── notebooks/
    ├── colab_backtest.ipynb                   # 백테스트(장기보유·리밸런싱·Ablation) · PDF/HTML 리포트
    ├── colab_screener.ipynb                   # 매년 4~5월 전 종목 스크리닝 · 보유주 검진 · 매수 배분 · PDF/HTML 리포트
    ├── colab_alpha_lab.ipynb                  # 알파 팩터 연구실(Walk-Forward 전진검증 · 몬테카를로 · PIT 시총 필터)
    ├── colab_pit_builder.ipynb                # 코스피·코스닥 전체 보통주(~2,300개사) 풀 유니버스 PIT DB 빌더
    ├── colab_final_portfolio_backtest.ipynb   # 최종 통합 백테스트 (9.7년 클린 PIT + A+B+C 적립식 + 14.7년 장기 추정)
    ├── colab_blind_oos_lab.ipynb              # 진성 블라인드(백지상태) OOS 검증실 (탐색/봉인 분리 · 사후편향 측정)
    ├── colab_qualitative_ai_monitor.ipynb     # OpenDART 공시 원문 + IR PDF × Gemini AI 정성 리스크 감시기 (⑤⑥⑦)
    └── colab_preferred_tracker.ipynb          # 우선주 vs 보통주 10년 총수익 · 괴리율 백분위 · 신규 매수 판독기
```

---

## 백테스트 비교 전략 (전략 A vs 전략 B)

| 구분 | 전략 A (현행 정량 규칙 재현) | 전략 B (배당 함정 보완안) |
|---|---|---|
| **배당 이력 (1번)** | 최근 5년 연속 지급 · 무삭감 · DPS 5년 성장 > 0 *(과거 시점 자료 창 보정)* | 동일 + **정기 DPS 5년 CAGR ≥ 3.0%** |
| **재무·환원 품질 (2~8번)** | 2~8번 정량 판정 (`FAIL 0`, `WARN ≤ 2`, 일반/금융 경로 분리) | 동일 |
| **배당 함정·유동성 필터** | 없음 | ① **배당수익률 2.0% ~ 12.0%**<br>② **60일 평균 일거래대금 ≥ 10억 원**<br>③ **52주 고점 대비 -35% 이하 & 최근 EPS 역성장 동시 시 제외** |
| **우선순위 정렬 (C층)** | `밸류 할인 순위 + DPS 5년 성장률 순위` 합 (원형 산술평균) | 동일하되 **밸류 괴리율 ±50% 캡(윈저라이징)** 적용 |
| **운용 규칙** | 매년 4월 첫 영업일 리밸런싱(`2020-04 ~ 2025-04`), 상위 10종목 동일가중, 배당금 재투자(DRIP), 편도 거래비용 0.25%, 배당소득세 15.4% | 동일 |

---

## Colab 실행 시 자동으로 수행되는 작업

1. **한글 폰트 자동 설치 및 설정:** `fonts-nanum` 설치 및 `matplotlib` `NanumGothic` 자동 적용.
2. **공개 시점고정(Point-in-Time) 재무·배당 데이터 로드:** GitHub에서 `data/` 자동 다운로드(DART API 키 불필요).
3. **일별 수정주가·거래대금·KOSPI 실시간 수집:** 네이버 금융 `fchart`(무인증) 우선 수집 + `FinanceDataReader` / `pykrx` 자동 폴백.
4. **일별 백테스트 및 시각화 출력:**
   - 누적 수익률 추이 (전략 A vs 전략 B vs KOSPI)
   - 일별 최대 낙폭(Drawdown / MDD) 차트
   - 연도별 세후 배당금 수령액 추이 (초기 투자금 1억 원 기준)
   - 연도별 수익률 비교 막대 차트
   - 핵심 성과표 (CAGR, 누적수익률, MDD, 연변동성, 국고채 10년물 반영 샤프지수, 승률, 평균 배당수익률)
   - 연도별 선정 종목 리스트 및 전략 B 필터링 제외 사유 분석

---

## 데이터 출처 및 한계 고지

- **공개 데이터 전용:** 본 프로젝트는 한국 상장사의 공개 공시(OpenDART 집계) 및 공개 시장 시세 데이터만 사용하며, 어떠한 개인 보유종목·계좌·자산 정보도 포함하지 않습니다.
- **생존 편향(Survivorship Bias):** 374개사 표본은 2026년 11월 기준 상장 생존 기업을 역추적한 목록이므로, `2019~2025` 기간 중 상장폐지·합병된 기업은 제외되어 있습니다.
- **벤치마크 기준:** KOSPI 일별 지수는 배당 미포함 가격지수(Price Return)이므로, 배당 재투자(DRIP) 포트폴리오와 비교 시 배당수익률(연 약 1.5~2.0% 수준) 차이를 함께 고려해야 합니다.
