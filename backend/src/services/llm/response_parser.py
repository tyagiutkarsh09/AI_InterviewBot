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
        flags=flags,
    )
