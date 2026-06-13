import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedLLMResponse:
    action: str
    spoken_text: str
    internal_notes: str
    score: Optional[float]
    score_topic: Optional[str]
    reasoning: Optional[str]
    next_state: str
    confidence: Optional[float] = None
    flags: list[str] = field(default_factory=list)


def parse_xml_response(raw: str) -> ParsedLLMResponse:
    start = raw.find("<interviewer_response>")
    end = raw.find("</interviewer_response>")

    if start == -1 or end == -1:
        return ParsedLLMResponse(
            action="acknowledge",
            spoken_text=raw.strip(),
            internal_notes="",
            score=None,
            score_topic=None,
            reasoning=None,
            next_state="questioning",
            flags=[],
        )

    xml_str = raw[start : end + len("</interviewer_response>")]

    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return ParsedLLMResponse(
            action="acknowledge",
            spoken_text=raw.strip(),
            internal_notes="",
            score=None,
            score_topic=None,
            reasoning=None,
            next_state="questioning",
            flags=[],
        )

    score_elem = root.find("score_update")
    score: Optional[float] = None
    score_topic: Optional[str] = None
    reasoning: Optional[str] = None

    if score_elem is not None:
        raw_score = score_elem.findtext("score", "").strip()
        if raw_score:
            try:
                parsed = float(raw_score)
                if 0 <= parsed <= 10:
                    score = parsed
            except ValueError:
                pass
        score_topic = score_elem.findtext("topic", "").strip() or None
        reasoning = score_elem.findtext("reasoning", "").strip() or None

    confidence: Optional[float] = None
    raw_confidence = root.findtext("confidence", "").strip()
    if raw_confidence:
        try:
            parsed_conf = float(raw_confidence)
            if 0.0 <= parsed_conf <= 1.0:
                confidence = parsed_conf
        except ValueError:
            pass

    flags_text = root.findtext("flags", "").strip()
    flags = [f.strip() for f in flags_text.split(",") if f.strip()]

    return ParsedLLMResponse(
        action=root.findtext("action", "acknowledge").strip(),
        spoken_text=root.findtext("spoken_text", "").strip(),
        internal_notes=root.findtext("internal_notes", "").strip(),
        score=score,
        score_topic=score_topic,
        reasoning=reasoning,
        next_state=root.findtext("next_state", "questioning").strip(),
        confidence=confidence,
        flags=flags,
    )


# Pattern A: conjunction that appears *after* a '?' (Shape A — two explicit question marks).
# e.g. "What is X? And also what is Y?"
_CONJUNCTION_AFTER_QMARK_RE = re.compile(
    r"\?[\s,]*(?:and also|and|as well as|along with)\b",
    re.IGNORECASE,
)

# Pattern B: conjunction that appears in the *body* of the sentence before the final '?'
# (Shape B — one shared terminal '?').
# e.g. "What is your notice period, and also are you open to relocation?"
# We require at least a few words both before and after the conjunction so that
# innocent uses like "X and Y?" (a single question about two related things) are
# not falsely flagged.  The heuristic: 3+ non-conjunction words before the match
# and 3+ words after it before the final '?'.
_CONJUNCTION_IN_BODY_RE = re.compile(
    r"(?:\w+\W+){3,}(?P<conj>and also|as well as|along with|and)\s+(?:\w+\W+){2,}\w+\?",
    re.IGNORECASE,
)


def validate_single_question(spoken_text: str) -> str:
    """Enforce that spoken_text contains only one question per turn.

    Two shapes of compound questions are detected and repaired:

    Shape A — multiple '?' (the LLM included two explicit question sentences):
        Truncated after the first '?' regardless of conjunction presence.

    Shape B — single shared terminal '?' with a compound conjunction
    ("and also", "as well as", "along with", or "and") in the body:
        Truncated before the conjunction, then a '?' is appended to preserve
        the first question's interrogative nature.

    Single questions — including those with clarifying sub-clauses that use
    a single '?' — are returned unchanged.

    Edge cases:
    - Empty string or no '?' → returned unchanged.
    """
    if not spoken_text:
        return spoken_text

    question_mark_count = spoken_text.count("?")

    # Shape A: two or more explicit question marks → truncate after first.
    if question_mark_count >= 2:
        first_q = spoken_text.find("?")
        return spoken_text[: first_q + 1].rstrip()

    if question_mark_count == 0:
        # No question at all — pass through (acknowledgement, statement, etc.).
        return spoken_text

    # question_mark_count == 1 from here.
    # Shape B: single terminal '?' with a compound conjunction in the body.
    # Order matters: check longer/more-specific conjunctions before shorter ones
    # to avoid "and also" being split on "and".
    conjunctions = ["and also", "as well as", "along with"]
    text_lower = spoken_text.lower()
    for conj in conjunctions:
        idx = text_lower.find(conj)
        if idx == -1:
            continue
        # Verify there is meaningful content before the conjunction (heuristic:
        # at least one word of 3+ chars before it) and meaningful content after
        # it before the final '?' (at least one word of 3+ chars after it).
        before = spoken_text[:idx].strip(" ,")
        after = spoken_text[idx + len(conj):].strip(" ,?")
        before_words = [w for w in before.split() if len(w) >= 3]
        after_words = [w for w in after.split() if len(w) >= 3]
        if len(before_words) >= 2 and len(after_words) >= 2:
            # Truncate before the conjunction and re-add '?'.
            return before.rstrip(" ,") + "?"

    return spoken_text
