# 💾 레벨 3: Git과 GitHub (게임 세이브 포인트)

> 🎯 **한 줄 요약:** 게임에서 보스 앞에 **세이브**해 두듯이, 코드도 잘될 때마다 저장해 두고 망치면 되돌아가요.

## ⏱️ 예상 기간

| 빠른 트랙 | 기본 트랙 | 여유 트랙 |
|---|---|---|
| 1주 | 2주 | 3주 |

← 이전: [레벨 2 Python](02-레벨2-파이썬-코딩-기초.md) · 다음: [레벨 4 에이전트 첫 만남](04-레벨4-AI-코딩-에이전트-첫만남.md) →

---

## ❓ 왜 배우나요?

- **git은 AI 에이전트 작업의 안전망이에요.** roadmap.sh vibe-coding 로드맵은 이렇게 말해요.
  - "**AI 작업이 성공할 때마다** git commit을 자주 하라"
  - "되돌려야 하면 AI의 되돌리기 기능보다 **git을 써라**"
  - "새 기능은 **깨끗한 git 상태**에서 시작하라"
- Claude Code의 체크포인트(`/rewind`)와 Cursor 체크포인트는 편리하지만, **git을 대신하지 않아요.** Claude Code 문서는 체크포인트가 Bash 명령으로 바뀐 파일은 추적하지 않는다고 해요. Cursor 체크포인트도 git과 별개로 내 컴퓨터에만 저장돼요.
- 에이전트가 무엇을 바꿨는지 보려면 **diff를 읽을 줄** 알아야 해요.

---

## 📚 핵심 개념

### 1. 저장소와 커밋 (Repository, Commit)

- 🧒 **비유:** **저장소**는 게임 **세이브 파일 묶음**, **커밋**은 **세이브 포인트 하나**예요. 세이브할 때 "보스 앞"처럼 **메모**를 남겨요.
- 📖 **정확히:** 저장소(repo)는 git이 기록을 관리하는 폴더예요(`.git` 폴더가 숨어 있어요). 커밋은 그 순간 파일들의 스냅샷 + 메시지 + 작성자 + 시간이에요.

### 2. 스테이징 (Staging, `git add`)

- 🧒 **비유:** 사진 찍기 전에 **사진에 나올 친구들을 무대 위로 부르는 것**이에요. 무대에 오른 것만 찍혀요.
- 📖 **정확히:** `git add`로 고른 변경만 다음 커밋에 들어가요. `git status`로 무대 위(staged)와 아래(unstaged)를 확인해요.

```text
작업 폴더 ──git add──▶ 스테이징(무대) ──git commit──▶ 저장소(세이브)
 (고치는 중)            (찍힐 준비)                    (영구 기록)
```

### 3. diff (차이 보기)

- 🧒 **비유:** **틀린 그림 찾기**예요. 전과 후를 나란히 놓고 뭐가 바뀌었는지 보여 줘요.
- 📖 **정확히:** `-`로 시작하는 빨간 줄은 **지워진 줄**, `+`로 시작하는 초록 줄은 **추가된 줄**이에요.

```diff
 def add(a, b):
-    return a - b
+    return a + b
```

> 에이전트 시대에는 **diff 읽기 = 숙제 검사**예요. 에이전트가 "고쳤어요"라고 하면, 항상 diff를 봐요.

### 4. 브랜치와 병합 (Branch, Merge)

- 🧒 **비유:** 게임에서 **"평행 세계"** 를 만드는 거예요. 원래 세계(main)는 그대로 두고, 새 세계에서 마음껏 실험해요. 잘되면 원래 세계에 합쳐요(merge).
- 📖 **정확히:** 브랜치는 커밋 흐름의 갈래예요. 같은 줄을 두 브랜치가 다르게 고치면 **충돌(conflict)** 이 나고, 사람이 골라 줘야 해요.

```text
main:     A───B───────E (merge)
               \     /
feature:        C───D
```

### 5. GitHub와 PR (Pull Request)

- 🧒 **비유:** GitHub는 세이브 파일을 올려 두는 **인터넷 사물함**이에요. PR은 "제 평행 세계를 원래 세계에 합쳐도 될까요?" 하고 **선생님께 검사받는 신청서**예요.
- 📖 **정확히:** `git push`로 내 커밋을 GitHub에 올리고, PR을 열면 다른 사람(또는 AI 리뷰어)이 diff를 보고 의견을 달아요. 승인되면 merge해요. `gh`(GitHub CLI)를 쓰면 터미널에서 PR을 만들 수 있어요.

### 6. 되돌리기 (Restore, Revert)

- 🧒 **비유:** "**마지막 세이브로 돌아가기**"예요.
- 📖 **정확히:**

| 상황 | 명령 | 설명 |
|---|---|---|
| 아직 커밋 안 한 변경을 버리고 싶다 | `git restore 파일이름` | 마지막 커밋 상태로 되돌려요 (변경이 사라져요!) |
| 무대에 올린 것을 내리고 싶다 | `git restore --staged 파일이름` | 변경은 남기고 무대에서만 내려요 |
| 이미 한 커밋을 취소하고 싶다 | `git revert 커밋ID` | 그 커밋을 "반대로 하는" 새 커밋을 만들어요 (기록이 안전하게 남아요) |

> ⚠️ `git reset --hard`는 변경을 **영영** 지울 수 있어요. Codex 프롬프팅 가이드도 에이전트에게 "`git reset --hard` 같은 **파괴적 명령을 쓰지 말라**"고 지시하라고 해요. 초보 때는 쓰지 마세요.

---

## 🛠️ 실습

> 준비: `git --version`으로 git이 있는지 확인해요. GitHub 계정을 만들어요. (선택) GitHub CLI `gh`를 설치하고 `gh auth login`으로 로그인해요.

### 실습 1. 첫 저장소, 첫 세이브 (20분)

```bash
cd ~/ai-practice
mkdir git-practice && cd git-practice
git init
git config --global user.name "내 이름"        # 처음 한 번만
git config --global user.email "내이메일@example.com"   # 처음 한 번만
echo "# 나의 첫 저장소" > README.md
git status                 # README.md가 빨간색(무대 아래)
git add README.md
git status                 # 초록색(무대 위)
git commit -m "README 추가"
git log --oneline          # 세이브 포인트 확인
```

✅ **Done when:** `git log --oneline`에 커밋 1개가 보여요.

### 실습 2. diff 읽기 (20분)

1. 레벨 2의 `calc.py`를 이 폴더로 복사하고 커밋해요.
2. 일부러 `+`를 `-`로 바꿔요.
3. `git diff`를 실행하고, 빨간 줄과 초록 줄이 무엇인지 말해 보세요.
4. `git restore calc.py`로 되돌리고, `git diff`가 비었는지 확인해요.

✅ **Done when:** 바꾼 것을 diff로 확인하고, restore로 원래대로 돌렸어요.

### 실습 3. 평행 세계 만들기 (30분)

```bash
git switch -c feature-hello        # 새 브랜치 만들고 이동
echo 'print("평행 세계에서 안녕")' > hello.py
git add hello.py
git commit -m "hello.py 추가"
git switch main                     # 원래 세계로
ls                                  # hello.py가 없어요!
git merge feature-hello             # 합치기
ls                                  # 이제 있어요
```

✅ **Done when:** 브랜치에서 만든 파일이 main에 merge된 것을 `git log --oneline --graph`로 확인했어요.

### 실습 4. 일부러 충돌 내고 해결하기 (30분)

1. main에 있는지 확인해요: `git switch main`
2. 이 순서대로 해요.
   - `git switch -c fruit` → `README.md` 첫 줄을 "# 바나나"로 바꾸고 `git commit -am "바나나"`
   - `git switch main` → 같은 첫 줄을 "# 사과"로 바꾸고 `git commit -am "사과"`
   - `git merge fruit` → 💥 충돌!
3. 파일을 열면 `<<<<<<<`, `=======`, `>>>>>>>` 표시가 보여요. 원하는 내용만 남기고 표시를 지워요.
4. `git add README.md` → `git commit`

✅ **Done when:** 충돌 표시 세 가지의 뜻을 설명할 수 있고, 충돌을 해결한 merge 커밋이 생겼어요.

### 실습 5. GitHub에 올리고 PR 만들기 (40분)

1. GitHub에서 빈 저장소 `git-practice`를 만들어요.
2. 화면 안내에 나오는 `git remote add origin ...` 명령과 `git push -u origin main`을 실행해요.
3. 새 브랜치를 만들어 작은 변경을 커밋하고 push해요: `git push -u origin 브랜치이름`
4. GitHub 웹에서 **Compare & pull request**를 누르거나, `gh`가 있으면 `gh pr create`로 PR을 열어요.
5. PR의 **Files changed** 탭에서 diff를 읽고, 스스로 코멘트 하나를 달아 본 뒤 merge해요.

✅ **Done when:** GitHub에 merge된 PR이 1개 있어요.

### 실습 6. Learn Git Branching (60분, 놀이처럼)

웹 게임처럼 git을 연습하는 Learn Git Branching의 기초 단계(Introduction Sequence)를 끝내요.

✅ **Done when:** 기초 단계를 모두 깼어요.

---

## 🏁 마일스톤 체크리스트: 💾 첫 세이브 포인트

다 체크하면 [레벨 4](04-레벨4-AI-코딩-에이전트-첫만남.md)로 가요.

- [ ] **AI 없이** `init → add → commit → log`를 해낸다
- [ ] **AI 없이** `git diff` 결과의 `+`/`-` 줄을 읽고 무엇이 바뀌었는지 말할 수 있다
- [ ] 브랜치를 만들고 merge한 기록이 `git log --graph`에 있다
- [ ] 충돌(conflict)을 직접 해결한 적이 1번 이상 있다
- [ ] GitHub에 저장소가 있고, merge된 PR이 1개 이상 있다
- [ ] `git restore`와 `git revert`의 차이를 설명할 수 있다
- [ ] `log.md`를 GitHub 저장소에 올려서 매일 커밋하기 시작했다 (최소 5일 연속)

---

## 🧠 스스로 확인 문제

1. `git add`와 `git commit`의 차이를 사진 찍기로 비유해 보세요.
2. diff에서 `-`와 `+`는 각각 무엇인가요?
3. 에이전트가 망친 코드를 되돌릴 때 AI의 되돌리기보다 git을 쓰라고 하는 이유는?
4. PR은 왜 만드나요?
5. 브랜치는 언제 만드나요?

<details><summary>정답 보기</summary>

1. `git add`는 사진에 나올 친구들을 무대에 세우는 것, `git commit`은 찰칵 찍어서 앨범에 영구히 붙이는 것이에요.
2. `-`는 지워진 줄, `+`는 추가된 줄이에요.
3. AI 체크포인트는 일부 변경(예: Claude Code에서 Bash 명령으로 바뀐 파일)을 추적하지 못하고, Cursor 체크포인트도 git과 별개예요. git 기록이 가장 확실하고 오래 남아요.
4. 원래 코드(main)에 합치기 전에 다른 사람(또는 AI 리뷰어)이 diff를 검사하게 하려고요.
5. 새 기능이나 실험을 시작할 때요. main은 깨끗하게 두고 평행 세계에서 작업해요.

</details>

---

## ⚠️ 흔한 실수

| 실수 | 이렇게 해요 |
|---|---|
| 하루 종일 작업하고 커밋 한 번 | **작은 성공마다** 커밋 (roadmap.sh) |
| 커밋 메시지 "수정", "asdf" | "계산기에 0으로 나누기 처리 추가"처럼 **무엇을 왜** |
| `.env`나 비밀번호 파일을 커밋 | `.gitignore`에 넣어요. 이미 올렸다면 비밀번호를 **바꿔요** |
| main에서 바로 실험 | 브랜치를 만들어요 |
| 겁나서 `git reset --hard` 사용 | 초보 때는 `restore`와 `revert`만 |
| 충돌 표시(`<<<<<<<`)를 남긴 채 커밋 | 표시를 다 지웠는지 확인 |

---

## 🔗 자료

**🇰🇷 한국어**
- 🇰🇷 고퀄리티 개발 컨텐츠 모음 (한국어 개발 자료 목록, git 자료도 여기서 찾아보세요): https://github.com/Integerous/goQuality-dev-contents
- (조사에서 검증된 한국어 git 전용 교재는 찾지 못했어요.)

**git 배우기**
- Learn Git Branching (그림으로 배우는 git 게임): https://github.com/pcottle/learnGitBranching
- MIT Missing Semester 2026 "Version Control and Git": https://github.com/missing-semester/missing-semester
- The Odin Project `git/foundations_git`: https://github.com/TheOdinProject/curriculum
- freeCodeCamp `introduction-to-git-and-github`: https://github.com/freeCodeCamp/freeCodeCamp
- roadmap.sh `git-github-beginner`, `vibe-coding`: https://github.com/nilbuild/developer-roadmap
