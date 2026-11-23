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
from .transcribe import Segment

ME = "me"
UNKNOWN = "spk?"
JOIN_GAP_S = 1.0       # 같은 사람의 말이 이만큼 이내로 이어지면 한 발언으로 묶음
NEAREST_TURN_S = 1.0   # 단어가 어느 구간에도 안 걸리면 이 거리 안의 가장 가까운 구간을 씀


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


def assign_loop_segments(segments: list[Segment], turns: list[Turn]) -> list[Utterance]:
    """스피커 트랙 구간들에 참석자를 붙입니다."""
    lt = relabel_turns(turns)
    if not lt:
        # 화자 분리 결과가 없으면(말이 아주 짧은 경우 등) 모두 참석자 1 로 봅니다.
        return join_utterances([Utterance(s.start, s.end, "spk1", s.text, s.lang) for s in segments])
    pieces: list[Utterance] = []
    for seg in segments:
        words = [w for w in seg.words if w.text.strip()]
        if not words:
            spk = _speaker_at(seg.start, seg.end, lt, nearest_s=3.0) or UNKNOWN
            pieces.append(Utterance(seg.start, seg.end, spk, seg.text, seg.lang))
            continue
        labels: list[str | None] = [_speaker_at(w.start, w.end, lt) for w in words]
        # 못 붙인 단어는 앞(없으면 뒤) 단어의 화자를 따릅니다.
        for i in range(len(labels)):
            if labels[i] is None and i > 0:
                labels[i] = labels[i - 1]
        for i in range(len(labels) - 2, -1, -1):
            if labels[i] is None:
                labels[i] = labels[i + 1]
        if labels and labels[0] is None:
            seg_spk = _speaker_at(seg.start, seg.end, lt, nearest_s=3.0) or UNKNOWN
            labels = [seg_spk] * len(labels)
        labels = _smooth(labels, words)
        for spk, ws in _runs(labels, words):
            pieces.append(Utterance(ws[0].start, ws[-1].end, spk, _join_words([w.text for w in ws]), seg.lang))
    return join_utterances(pieces)


def _smooth(labels: list[str], words) -> list[str]:
    """같은 사람 말 사이에 낀 0.6초 미만·1단어짜리 다른 화자 표시는 앞뒤 화자로 고칩니다."""
    labels = list(labels)
    runs = _runs(labels, list(range(len(labels))))
    for k in range(1, len(runs) - 1):
        lab, idx = runs[k]
        prev_lab, next_lab = runs[k - 1][0], runs[k + 1][0]
        dur = words[idx[-1]].end - words[idx[0]].start
        if prev_lab == next_lab and len(idx) == 1 and dur < 0.6:
            for i in idx:
                labels[i] = prev_lab
    return labels


def mic_utterances(segments: list[Segment]) -> list[Utterance]:
    return join_utterances([Utterance(s.start, s.end, ME, s.text, s.lang) for s in segments])


def join_utterances(utts: list[Utterance], gap: float = JOIN_GAP_S) -> list[Utterance]:
    """시간순으로 놓고, 같은 사람이 gap 초 안에 이어 말한 것을 한 발언으로 묶습니다."""
    out: list[Utterance] = []
    for u in sorted(utts, key=lambda u: (u.start, u.end)):
        if out and out[-1].speaker == u.speaker and u.start - out[-1].end <= gap:
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
