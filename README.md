# Common_Everything

여러 독립 프로젝트·학습 자료·공개 실험실을 폴더 단위로 관리하는 **공개(Public) 모노레포**입니다.

---

## 프로젝트 목차

| 폴더 | 설명 | 주요 기술 / 실행 방법 | 문서 |
|---|---|---|---|
| [`ai-coding-curriculum/`](ai-coding-curriculum/) | 비전공자·입문자용 AI 코딩 학습 커리큘럼 (레벨 1~9, 치트시트, 용어사전) | Markdown 학습서 | [README](ai-coding-curriculum/README.md) |
| [`meeting-recorder/`](meeting-recorder/) | 오프라인 화상회의 녹음·전사·화자분리 데스크톱 프로그램 | Python, Whisper, Windows exe 빌드(GitHub Actions) | [README](meeting-recorder/README.md) |
| [`dividend-backtest/`](dividend-backtest/) | 한국 배당성장주 전략 검증 및 Google Colab 독립 백테스트 연구소 | Python, Jupyter/Colab, 공개 DART·일별 시세 | [README](dividend-backtest/README.md) · [Colab 열기](https://colab.research.google.com/github/khs0221/Common_Everything/blob/feat/dividend-backtest/dividend-backtest/notebooks/colab_backtest.ipynb) |

---

## 저장소 운영 규칙

1. **폴더별 독립성 유지:** 각 최상위 폴더는 자체 `README.md`, 코드, 데이터/설정, 테스트를 독립적으로 관리합니다.
2. **Public 저장소 보안 원칙:** 이 저장소는 **공개(Public)**입니다. 개인 보유종목·계좌·금액, API 키(`.env`, `DART_API_KEY`, 텔레그램 토큰 등), 비공개 설정은 절대 커밋하지 않습니다.
3. **브랜치 및 PR 흐름:**
   - 새 작업은 항상 기능 브랜치(`feat/<폴더명>-<작업>`, `fix/<폴더명>-<작업>`)에서 진행한 뒤 Pull Request로 검토·머지합니다.
   - *(권장)* GitHub 저장소 **Settings → Branches**에서 기본 브랜치(`copilot/ai-coding-curriculum`)를 `main`으로 이름 변경해 두면 관리가 더 깔끔합니다. (기존 `meeting-recorder-build.yml`은 `default_branch`를 동적으로 참조하므로 안전합니다.)
