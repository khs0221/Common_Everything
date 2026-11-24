"""글 옮기기 결과와 발언자 구분 결과를 합쳐 발언 목록을 만듭니다.

- 마이크 트랙의 말은 모두 '나'(me).
- 스피커 트랙의 말은 단어마다 그 시각에 말하던 참석자(화자 분리 결과)를 붙인 뒤,
  같은 참석자의 연속 단어를 한 발언으로 묶습니다. 한 whisper 구간 안에서 말하는 사람이
  바뀌어도 나눠집니다.
- 두 트랙의 발언을 시작 시각 순으로 합칩니다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .diarize import Turn
from .transcribe import Segment, Word, ends_sentence

ME = "me"
UNKNOWN = "spk?"
JOIN_GAP_S = 1.0       # 같은 사람의 말이 이만큼 이내로 이어지면 한 발언으로 묶음
NEAREST_TURN_S = 1.0   # 단어가 어느 구간에도 안 걸리면 이 거리 안의 가장 가까운 구간을 씀
SENTENCE_GAP_S = 1.5   # 단어 사이가 이만큼 비면 문장이 끝난 것으로 봄
MIN_SPLIT_WORDS = 3    # 한 문장 안에서 이 개수·시간 이상 다른 사람 말이 이어져야 나눔
MIN_SPLIT_S = 1.0
MAX_UTTERANCE_S = 60.0       # 한 줄(한 발언)의 최대 길이
MAX_UTTERANCE_CHARS = 600


@dataclass
class Utterance:
    start: float
    end: float
    speaker: str   # 'me', 'spk1', 'spk2', ..., 'spk?'
    text: str
    lang: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def relabel_turns(turns: list[Turn]) -> list[tuple[float, float, str]]:
    """화자 번호를 처음 나온 순서대로 spk1, spk2… 로 바꿉니다."""
    order: dict[int, str] = {}
    out = []
    for t in sorted(turns, key=lambda t: (t.start, t.end)):
        if t.speaker not in order:
            order[t.speaker] = f"spk{len(order) + 1}"
        out.append((t.start, t.end, order[t.speaker]))
    return out


def _speaker_at(a: float, b: float, turns: list[tuple[float, float, str]],
                nearest_s: float = NEAREST_TURN_S) -> str | None:
    """[a, b] 와 가장 많이 겹치는 화자. 안 겹치면 nearest_s 안의 가장 가까운 화자."""
    best, best_ov = None, 0.0
    for s, e, spk in turns:
        if s > b:
            break
        ov = min(b, e) - max(a, s)
        if ov > best_ov:
            best, best_ov = spk, ov
    if best is not None:
        return best
    mid = (a + b) / 2
    nearest, dist = None, nearest_s
    for s, e, spk in turns:
        d = s - mid if mid < s else (mid - e if mid > e else 0.0)
        if d <= dist:
            nearest, dist = spk, d
    return nearest


def _join_words(words: list[str]) -> str:
    return "".join(words).strip()


def _runs(labels: list[str], items: list) -> list[tuple[str, list]]:
    runs: list[tuple[str, list]] = []
    for lab, it in zip(labels, items):
        if runs and runs[-1][0] == lab:
            runs[-1][1].append(it)
        else:
            runs.append((lab, [it]))
    return runs


def _sentences(segments: list[Segment]) -> list[list[tuple[Word, str]]]:
    """트랙 전체 단어를 시간순으로 이어 문장 단위로 나눕니다.

    문장 끝 = 문장 부호(. ? ! 등) 또는 단어 사이가 SENTENCE_GAP_S 초 넘게 빔.
    whisper 구간 경계와 상관없이 나누므로, 구간 끝에 매달린 다음 문장의 첫 단어("… March. I")도
    다음 문장에 붙습니다.
    """
    items = [(w, seg.lang) for seg in sorted(segments, key=lambda s: s.start)
             for w in seg.words if w.text.strip()]
    sents: list[list[tuple[Word, str]]] = []
    cur: list[tuple[Word, str]] = []
    for w, lang in items:
        if cur and w.start - cur[-1][0].end > SENTENCE_GAP_S:
            sents.append(cur)
            cur = []
        cur.append((w, lang))
        if ends_sentence(w.text):
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    return sents


def assign_loop_segments(segments: list[Segment], turns: list[Turn]) -> list[Utterance]:
    """스피커 트랙에 참석자를 붙입니다.

    문장마다 단어 시각이 가장 많이 겹치는 참석자를 붙입니다. 한 문장 안에서 다른 참석자 쪽 단어가
    MIN_SPLIT_WORDS 개·MIN_SPLIT_S 초 이상 이어질 때만(문장 부호 없이 말이 바뀐 경우) 나눕니다.
    단어 시각은 경계 근처에서 흔들리므로 짧은 조각은 나누지 않습니다.
    """
    lt = relabel_turns(turns)
    if not lt:
        # 화자 분리 결과가 없으면(말이 아주 짧은 경우 등) 모두 참석자 1 로 봅니다.
        return join_utterances([Utterance(s.start, s.end, "spk1", s.text, s.lang) for s in segments])
    pieces: list[Utterance] = []
    for seg in segments:  # 단어 시각이 없는 구간(드묾)은 구간 단위로
        if not any(w.text.strip() for w in seg.words):
            spk = _speaker_at(seg.start, seg.end, lt, nearest_s=3.0) or UNKNOWN
            pieces.append(Utterance(seg.start, seg.end, spk, seg.text, seg.lang))
    for sent in _sentences(segments):
        words = [w for w, _ in sent]
        lang = sent[0][1]
        labels: list[str | None] = [_speaker_at(w.start, w.end, lt) for w in words]
        if all(lab is None for lab in labels):
            spk = _speaker_at(words[0].start, words[-1].end, lt, nearest_s=3.0) or UNKNOWN
            labels = [spk] * len(words)
        # 못 붙인 단어는 앞(없으면 뒤) 단어의 화자를 따릅니다.
        for i in range(1, len(labels)):
            if labels[i] is None:
                labels[i] = labels[i - 1]
        for i in range(len(labels) - 2, -1, -1):
            if labels[i] is None:
                labels[i] = labels[i + 1]
        major = _majority(words, labels, lt)
        runs = _runs(labels, words)
        split = any(lab != major and len(ws) >= MIN_SPLIT_WORDS and ws[-1].end - ws[0].start >= MIN_SPLIT_S
                    for lab, ws in runs)
        if not split:
            runs = [(major, words)]
        else:
            # 나눌 때도 짧은 조각은 이웃에 붙입니다.
            runs = _runs(_smooth_runs(labels, words), words)
        for spk, ws in runs:
            pieces.append(Utterance(ws[0].start, ws[-1].end, spk, _join_words([w.text for w in ws]), lang))
    return join_utterances(pieces)


def _majority(words, labels, lt) -> str:
    """문장 안에서 겹친 시간이 가장 긴 참석자."""
    score: dict[str, float] = {}
    for w, lab in zip(words, labels):
        for s, e, spk in lt:
            ov = min(w.end, e) - max(w.start, s)
            if ov > 0:
                score[spk] = score.get(spk, 0.0) + ov
    if score:
        return max(score, key=score.get)
    counts: dict[str, int] = {}
    for lab in labels:
        counts[lab] = counts.get(lab, 0) + 1
    return max(counts, key=counts.get)


def _smooth_runs(labels: list[str], words) -> list[str]:
    """MIN_SPLIT_WORDS 개 미만 또는 MIN_SPLIT_S 초 미만인 조각을 앞(없으면 뒤) 조각에 붙입니다."""
    labels = list(labels)
    changed = True
    while changed:
        changed = False
        runs = _runs(labels, list(range(len(labels))))
        if len(runs) < 2:
            break
        for k, (lab, idx) in enumerate(runs):
            dur = words[idx[-1]].end - words[idx[0]].start
            if len(idx) < MIN_SPLIT_WORDS or dur < MIN_SPLIT_S:
                target = runs[k - 1][0] if k > 0 else runs[k + 1][0]
                for i in idx:
                    labels[i] = target
                changed = True
                break
    return labels


def mic_utterances(segments: list[Segment]) -> list[Utterance]:
    return join_utterances([Utterance(s.start, s.end, ME, s.text, s.lang) for s in segments])


def join_utterances(utts: list[Utterance], gap: float = JOIN_GAP_S) -> list[Utterance]:
    """시간순으로 놓고, 같은 사람이 gap 초 안에 이어 말한 것을 한 발언으로 묶습니다.

    한 발언이 MAX_UTTERANCE_S 초 또는 MAX_UTTERANCE_CHARS 자를 넘으면 새 줄(새 시각)로 시작합니다
    (긴 독백이 한 줄·한 시각으로 뭉치지 않게, 감사 F11).
    """
    out: list[Utterance] = []
    for u in sorted(utts, key=lambda u: (u.start, u.end)):
        if (out and out[-1].speaker == u.speaker and u.start - out[-1].end <= gap
                and u.end - out[-1].start <= MAX_UTTERANCE_S
                and len(out[-1].text) + 1 + len(u.text) <= MAX_UTTERANCE_CHARS):
            prev = out[-1]
            prev.end = max(prev.end, u.end)
            prev.text = f"{prev.text} {u.text}".strip()
            prev.lang = prev.lang or u.lang
        else:
            out.append(Utterance(u.start, u.end, u.speaker, u.text, u.lang))
    return out


def merge_tracks(mic: list[Utterance], loop: list[Utterance]) -> list[Utterance]:
    """두 트랙을 시작 시각 순으로 합칩니다(같은 시각이면 마이크가 먼저)."""
    tagged = [(u.start, 0, i, u) for i, u in enumerate(mic)] + [(u.start, 1, i, u) for i, u in enumerate(loop)]
    return [t[3] for t in sorted(tagged, key=lambda t: (t[0], t[1], t[2]))]


def speakers_in(utts: list[Utterance]) -> list[str]:
    """등장하는 참석자 키(나 제외), 번호 순."""
    keys = {u.speaker for u in utts if u.speaker != ME}

    def k(s: str):
        tail = s[3:]
        return (0, int(tail)) if tail.isdigit() else (1, 0)

    return sorted(keys, key=k)


def representative(utts: list[Utterance], speaker: str, n: int = 2, max_chars: int = 160) -> list[Utterance]:
    """이름을 정할 때 보여 줄 대표 발언(긴 발언 우선, 시간순으로 반환)."""
    cands = [u for u in utts if u.speaker == speaker and u.text]
    cands.sort(key=lambda u: -len(u.text))
    picked = sorted(cands[:n], key=lambda u: u.start)
    out = []
    for u in picked:
        t = u.text if len(u.text) <= max_chars else u.text[:max_chars].rstrip() + "…"
        out.append(Utterance(u.start, u.end, u.speaker, t, u.lang))
    return out
