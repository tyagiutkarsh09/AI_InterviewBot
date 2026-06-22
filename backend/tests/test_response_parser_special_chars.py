"""Regression guards: the interviewer LLM must never speak its internal reasoning.

WHY: A bare ``&`` in an LLM-generated field (e.g. ``<topic>Performance optimization &
database design</topic>``) makes the XML not well-formed. ``ET.fromstring`` then raises
ParseError and the old fallback set ``spoken_text = raw.strip()`` -- so the candidate
heard the ENTIRE response, including ``internal_notes`` and ``score_update``. These tests
encode the invariant that internal-only content (internal_notes, score_update, raw tags)
can never reach ``spoken_text``, regardless of how the XML is mangled.
"""
from src.services.llm.response_parser import parse_xml_response


# The exact shape that leaked in production: a bare '&' in the <topic>.
_RESPONSE_WITH_BARE_AMPERSAND = """```xml
<interviewer_response>
<action>follow_up</action>
<spoken_text>No problem. Let me break this down a bit. Which part feels unclear?</spoken_text>
<internal_notes>The candidate may not have actually performed this work. Possible experience gap.</internal_notes>
<confidence>0.65</confidence>
<score_update>
<topic>Performance optimization & database design</topic>
<score>1</score>
<reasoning>Candidate expressed inability to answer.</reasoning>
</score_update>
<next_state>questioning</next_state>
<flags>candidate_uncertainty, possible_experience_gap</flags>
</interviewer_response>
```"""


def test_bare_ampersand_in_topic_parses_spoken_text_only():
    """The bare '&' must be tolerated; only spoken_text reaches the candidate."""
    parsed = parse_xml_response(_RESPONSE_WITH_BARE_AMPERSAND)

    assert parsed.spoken_text == (
        "No problem. Let me break this down a bit. Which part feels unclear?"
    )
    assert parsed.action == "follow_up"
    # The '&' field still parses into its real value (entity-decoded).
    assert parsed.score_topic == "Performance optimization & database design"
    assert parsed.score == 1.0


def test_bare_ampersand_never_leaks_internal_notes_to_spoken_text():
    """The catastrophic invariant: internal reasoning is never spoken."""
    parsed = parse_xml_response(_RESPONSE_WITH_BARE_AMPERSAND)

    assert "internal_notes" not in parsed.spoken_text
    assert "experience gap" not in parsed.spoken_text
    assert "score_update" not in parsed.spoken_text
    assert "```" not in parsed.spoken_text


def test_unrepairable_xml_with_tags_extracts_spoken_text_not_raw_blob():
    """A break the &-escape can't fix (bare '<' in internal_notes) must still not
    dump the raw blob: regex-extract spoken_text instead."""
    raw = """<interviewer_response>
<action>follow_up</action>
<spoken_text>Could you tell me more about that?</spoken_text>
<internal_notes>Candidate said latency < 5ms which seems off.</internal_notes>
<next_state>questioning</next_state>
</interviewer_response>"""
    parsed = parse_xml_response(raw)

    assert parsed.spoken_text == "Could you tell me more about that?"
    assert "internal_notes" not in parsed.spoken_text
    assert "Candidate said" not in parsed.spoken_text


def test_plain_prose_with_no_tags_still_passes_through():
    """Preserve the existing fallback for genuine plain-text responses."""
    parsed = parse_xml_response("Sure, let's keep going.")
    assert parsed.action == "acknowledge"
    assert parsed.spoken_text == "Sure, let's keep going."
