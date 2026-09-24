"""
Deterministic parser for FOMC transcript Q->A blocks.

The parser reads timestamped transcript segments and builds speaker utterances.
It then emits question->Powell answer blocks anchored on Powell answer start.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Sequence


POWELL_SPEAKERS = {
    "CHAIR POWELL",
    "POWELL",
    "JEROME POWELL",
}

# Typical all-caps speaker tokens in fed transcripts:
# "MICHELLE SMITH.", "JEANNA SMIALEK.", "CHAIR POWELL."
SPEAKER_PATTERN = re.compile(
    r"(?<![A-Z])((?:CHAIR\s+POWELL|POWELL|[A-Z][A-Z'\-]+(?:\s+[A-Z][A-Z'\-]+)+))\."
)


@dataclass
class Utterance:
    speaker: str
    start_s: float
    end_s: float
    text: str


@dataclass
class QABlock:
    answer_idx: int
    question_speaker: str
    question_speakers: List[str]
    question_start_s: float
    question_end_s: float
    question_text: str
    answer_speaker: str
    answer_start_s: float
    answer_end_s: float
    answer_text: str
    parse_confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "answer_idx": self.answer_idx,
            "question_speaker": self.question_speaker,
            "question_speakers": self.question_speakers,
            "question_start_s": self.question_start_s,
            "question_end_s": self.question_end_s,
            "question_text": self.question_text,
            "answer_speaker": self.answer_speaker,
            "answer_start_s": self.answer_start_s,
            "answer_end_s": self.answer_end_s,
            "answer_text": self.answer_text,
            "parse_confidence": self.parse_confidence,
        }


def _normalize_speaker(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip()).upper()


def _is_powell(speaker: str) -> bool:
    return _normalize_speaker(speaker) in POWELL_SPEAKERS


def _piece_start(seg_start: float, seg_duration: float, seg_text_len: int, char_idx: int) -> float:
    if seg_text_len <= 0 or seg_duration <= 0:
        return seg_start
    ratio = max(0.0, min(1.0, float(char_idx) / float(seg_text_len)))
    return seg_start + (seg_duration * ratio)


def _split_segment_by_speaker(seg: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = str(seg.get("text", "") or "")
    if not text.strip():
        return []

    seg_start = float(seg.get("start", 0.0) or 0.0)
    seg_duration = float(seg.get("duration", 0.0) or 0.0)
    text_len = len(text)
    out: List[Dict[str, Any]] = []

    matches = list(SPEAKER_PATTERN.finditer(text))
    if not matches:
        out.append(
            {
                "speaker": None,
                "start_s": seg_start,
                "end_s": seg_start + seg_duration,
                "text": text.strip(),
            }
        )
        return out

    cursor = 0
    current_speaker = None
    current_start = seg_start

    for m in matches:
        speaker = _normalize_speaker(m.group(1))
        speaker_char_start = m.start(1)
        piece_text = text[cursor : m.start()].strip()
        piece_end = _piece_start(seg_start, seg_duration, text_len, m.start())
        if piece_text:
            out.append(
                {
                    "speaker": current_speaker,
                    "start_s": current_start,
                    "end_s": piece_end,
                    "text": piece_text,
                }
            )
        current_speaker = speaker
        cursor = m.end()
        current_start = _piece_start(seg_start, seg_duration, text_len, speaker_char_start)

    tail = text[cursor:].strip()
    out.append(
        {
            "speaker": current_speaker,
            "start_s": current_start,
            "end_s": seg_start + seg_duration,
            "text": tail,
        }
    )

    return out


def build_utterances(segments: Sequence[Dict[str, Any]]) -> List[Utterance]:
    utterances: List[Utterance] = []
    current_speaker: str | None = None

    for seg in sorted(segments, key=lambda s: float(s.get("start", 0.0) or 0.0)):
        pieces = _split_segment_by_speaker(seg)
        for piece in pieces:
            speaker = piece["speaker"] if piece["speaker"] else current_speaker
            if not speaker:
                # Unknown speaker before first explicit marker.
                speaker = "UNKNOWN"
            current_speaker = speaker

            text = str(piece["text"]).strip()
            if not text:
                continue
            start_s = float(piece["start_s"])
            end_s = float(piece["end_s"])

            if utterances and utterances[-1].speaker == speaker and start_s <= utterances[-1].end_s + 1.5:
                # Merge adjacent same-speaker snippets.
                merged = utterances[-1]
                merged.text = f"{merged.text} {text}".strip()
                merged.end_s = max(merged.end_s, end_s)
            else:
                utterances.append(Utterance(speaker=speaker, start_s=start_s, end_s=end_s, text=text))

    return utterances


def extract_qa_blocks(segments: Sequence[Dict[str, Any]]) -> List[QABlock]:
    utterances = build_utterances(segments)
    qa_blocks: List[QABlock] = []
    pending_question: List[Utterance] = []
    answer_idx = 0
    i = 0

    while i < len(utterances):
        u = utterances[i]
        if _is_powell(u.speaker):
            j = i
            answer_parts: List[Utterance] = []
            while j < len(utterances) and _is_powell(utterances[j].speaker):
                answer_parts.append(utterances[j])
                j += 1

            if pending_question and answer_parts:
                q_speakers_ordered: List[str] = []
                for q in pending_question:
                    if q.speaker not in q_speakers_ordered:
                        q_speakers_ordered.append(q.speaker)

                q_text = " ".join(q.text for q in pending_question).strip()
                a_text = " ".join(a.text for a in answer_parts).strip()
                has_named_questioner = any(s != "UNKNOWN" for s in q_speakers_ordered)
                if q_text and a_text and has_named_questioner:
                    answer_idx += 1
                    parse_conf = 1.0
                    if any(s == "UNKNOWN" for s in q_speakers_ordered):
                        parse_conf = 0.7
                    qa_blocks.append(
                        QABlock(
                            answer_idx=answer_idx,
                            question_speaker=q_speakers_ordered[-1] if q_speakers_ordered else "UNKNOWN",
                            question_speakers=q_speakers_ordered,
                            question_start_s=pending_question[0].start_s,
                            question_end_s=pending_question[-1].end_s,
                            question_text=q_text,
                            answer_speaker=answer_parts[0].speaker,
                            answer_start_s=answer_parts[0].start_s,
                            answer_end_s=answer_parts[-1].end_s,
                            answer_text=a_text,
                            parse_confidence=parse_conf,
                        )
                    )

            pending_question = []
            i = j
            continue

        # Non-Powell utterance goes into pending question buffer.
        if u.speaker == "UNKNOWN" and not pending_question:
            i += 1
            continue
        pending_question.append(u)
        i += 1

    return qa_blocks


def qa_blocks_to_rows(blocks: Iterable[QABlock]) -> List[Dict[str, Any]]:
    return [b.to_dict() for b in blocks]

