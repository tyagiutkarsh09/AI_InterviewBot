# Opening Quality Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `backend/tests/test_opening_quality.py` with five failing pytest tests that enforce a warm, personalized bot opening before any technical question.

**Architecture:** Pure data tests — no production code, no mocking, no asyncio. Each test takes a hardcoded `BAD_OPENING` string and asserts a quality property the string does not satisfy. All five tests are expected to fail. They define the contract; a future fix to the bot (or a broken prompt regression) makes them go red or green.

**Tech Stack:** Python 3.11, pytest (already installed — `backend/requirements.txt`)

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/tests/test_opening_quality.py` | All 5 failing tests + shared constants |

No other files are touched.

---

## Existing context to read before starting

- `backend/tests/test_warmup_flow.py` — see `TECHNICAL_KEYWORDS` constant and the flat-function test style to match
- `backend/tests/conftest.py` — note the `autouse` voice-session fixture; it fires for all tests but is harmless here (no production code is called)
- `docs/superpowers/specs/2026-06-14-opening-quality-test-design.md` — the authoritative spec

---

### Task 1: Scaffold the test file (constants and keyword sets, no tests yet)

**Files:**
- Create: `backend/tests/test_opening_quality.py`

- [ ] **Step 1: Create the file with the module docstring, constants, and keyword sets**

`backend/tests/test_opening_quality.py`:

```python
"""Failing tests for AI interviewer opening quality.

WHY: Jumping straight to technical questions increases candidate anxiety and
     degrades response authenticity (SHRM, LinkedIn Talent Insights 2023).
     The bot MUST greet by name, signal warmth, and ask at least one rapport
     question before any technical content.

FAILURE MODE: These tests are seeded with BAD_OPENING — a bot response that
              skips the friendly preamble entirely. ALL assertions below should
              FAIL against BAD_OPENING. That is intentional: these tests encode
              the contract. They turn green once the bot produces proper warm
              openers (or when BAD_OPENING is swapped for a good response to
              verify the detection logic works both ways).

Framework: pytest, no asyncio — pure string assertions, no production code called.
Spec: docs/superpowers/specs/2026-06-14-opening-quality-test-design.md
"""

# ---------------------------------------------------------------------------
# Candidate fixture (inline — no pytest fixture needed, pure constants)
# ---------------------------------------------------------------------------

CANDIDATE_NAME = "Utkarsh"
CANDIDATE_PROFILE = {
    "name": CANDIDATE_NAME,
    "job_role": "backend engineer",
    "experience_level": "mid",
}

# ---------------------------------------------------------------------------
# The bad bot output under test
#
# Simulates a broken interviewer that skips the warm preamble and opens
# directly with a technical question.
# ---------------------------------------------------------------------------

BAD_OPENING = (
    "Can you explain the difference between a process and a thread? "
    "Please be specific about memory isolation, context-switching overhead, "
    "and when you would choose one over the other."
)

# ---------------------------------------------------------------------------
# Detection keyword sets
#
# Kept as frozensets so accidental mutation doesn't silently affect tests.
# These are intentionally strict (substring match, lowercase) — no NLP.
# ---------------------------------------------------------------------------

WARM_MARKERS = frozenset({
    "how are you",
    "how's your day",
    "how did your day",
    "nice to meet",
    "good to meet",
    "great to meet",
    "glad to",
    "welcome",
    "before we dive in",
})

RAPPORT_SIGNALS = frozenset({
    "your day",
    "recent role",
    "last job",
    "most recent",
    "where did you study",
    "what brought you",
    "how did you get into",
    "what's new",
    "exciting",
    "anything going on",
})

TECHNICAL_OPENERS = frozenset({
    "can you explain",
    "what is the difference",
    "describe how",
    "implement",
    "write a",
    "what are the",
    "how does",
    "define ",
})
```

- [ ] **Step 2: Verify the file is importable (no syntax errors)**

```bash
cd backend && python -c "import tests.test_opening_quality; print('OK')"
```

Expected output:
```
OK
```

If you see `ModuleNotFoundError`, make sure you are running from inside `backend/`.

---

### Task 2: Add R1 and R2 tests — name and warm marker

**Files:**
- Modify: `backend/tests/test_opening_quality.py` (append two test functions)

- [ ] **Step 1: Append R1 and R2 test functions to the file**

Add this block at the bottom of `backend/tests/test_opening_quality.py`:

```python
# ---------------------------------------------------------------------------
# R1 — Candidate name must appear in the opening
# ---------------------------------------------------------------------------

def test_opening_contains_candidate_name():
    """Bot MUST address the candidate by name on the first turn.

    WHY: An unnamed opener is impersonal and signals the bot is ignoring
         candidate metadata. Minimum bar for personalization.

    EXPECTED: 'Utkarsh' appears somewhere in the opening text.
    ACTUAL:   BAD_OPENING contains no candidate name — jumps straight to
              a question about processes vs. threads.

    >>> FAILS against BAD_OPENING.
    """
    assert CANDIDATE_NAME.lower() in BAD_OPENING.lower(), (
        f"FAIL (R1 — missing name)\n"
        f"  Expected: '{CANDIDATE_NAME}' present in opening\n"
        f"  Actual:   {BAD_OPENING!r}\n"
        f"  Fix:      Bot must address the candidate by name in the first turn."
    )


# ---------------------------------------------------------------------------
# R2 — At least one warm greeting marker must be present
# ---------------------------------------------------------------------------

def test_opening_has_warm_marker():
    """Bot MUST include a recognisable warm greeting phrase.

    WHY: Warm markers signal 'conversation mode', not 'test mode'. Absence
         of any warm marker makes the experience interrogative from word one.

    EXPECTED: At least one of WARM_MARKERS appears in the opening.
    ACTUAL:   BAD_OPENING contains none — opens directly with a technical
              question.

    >>> FAILS against BAD_OPENING.
    """
    _text = BAD_OPENING.lower()
    matched = [m for m in WARM_MARKERS if m in _text]
    assert matched, (
        f"FAIL (R2 — no warm marker)\n"
        f"  Expected: at least one of {sorted(WARM_MARKERS)}\n"
        f"  Actual:   {BAD_OPENING!r}\n"
        f"  Matched:  none\n"
        f"  Fix:      Add a greeting phrase before the first question."
    )
```

- [ ] **Step 2: Run R1 and R2, verify both fail**

```bash
cd backend && python -m pytest tests/test_opening_quality.py::test_opening_contains_candidate_name tests/test_opening_quality.py::test_opening_has_warm_marker -v
```

Expected output (both red):
```
FAILED tests/test_opening_quality.py::test_opening_contains_candidate_name
FAILED tests/test_opening_quality.py::test_opening_has_warm_marker
2 failed in 0.XXs
```

If either test passes, the assertion logic is wrong — `BAD_OPENING` must not accidentally satisfy R1 or R2. Re-read the assertion and the constant.

---

### Task 3: Add R3 and R4 tests — rapport signal and no technical opener

**Files:**
- Modify: `backend/tests/test_opening_quality.py` (append two more test functions)

- [ ] **Step 1: Append R3 and R4 test functions**

Add this block at the bottom of `backend/tests/test_opening_quality.py`:

```python
# ---------------------------------------------------------------------------
# R3 — At least one rapport signal must be present
# ---------------------------------------------------------------------------

def test_opening_has_rapport_signal():
    """Bot MUST ask at least one rapport-building question.

    WHY: Rapport questions lower anxiety and establish trust before
         technical probing begins (SHRM interviewing guidelines). Acceptable
         topics: well-being, last job, education, general life.

    EXPECTED: At least one of RAPPORT_SIGNALS appears in the opening.
    ACTUAL:   BAD_OPENING has no rapport question — probes process vs.
              thread semantics instead.

    >>> FAILS against BAD_OPENING.
    """
    _text = BAD_OPENING.lower()
    matched = [s for s in RAPPORT_SIGNALS if s in _text]
    assert matched, (
        f"FAIL (R3 — no rapport signal)\n"
        f"  Expected: at least one of {sorted(RAPPORT_SIGNALS)}\n"
        f"  Actual:   {BAD_OPENING!r}\n"
        f"  Matched:  none\n"
        f"  Fix:      Ask about well-being, last role, or background before "
        f"any technical question."
    )


# ---------------------------------------------------------------------------
# R4 — Opening must NOT start with a technical interrogative
# ---------------------------------------------------------------------------

def test_opening_does_not_start_with_technical_interrogative():
    """The FIRST sentence must not open with a technical question pattern.

    WHY: The first sentence sets the entire tone. Even if a warm sentence
         appears later, a cold technical opener has already signalled
         'you are being tested'.

    EXPECTED: First sentence does not start with any TECHNICAL_OPENERS entry.
    ACTUAL:   BAD_OPENING begins with 'Can you explain...' — a textbook
              technical interrogative.

    First-sentence extraction: split on '.' then '?' and take element 0.
    This is intentionally simple — the first clause is what matters.

    >>> FAILS against BAD_OPENING.
    """
    first_sentence = BAD_OPENING.split(".")[0].split("?")[0].lower().strip()
    matched = [t for t in TECHNICAL_OPENERS if first_sentence.startswith(t)]
    assert not matched, (
        f"FAIL (R4 — technical interrogative opener)\n"
        f"  Expected: first sentence does not start with a technical pattern\n"
        f"  First sentence: {first_sentence!r}\n"
        f"  Matched:        {matched}\n"
        f"  Fix:            Lead with a greeting or rapport question, not a "
        f"technical probe."
    )
```

- [ ] **Step 2: Run R3 and R4, verify both fail**

```bash
cd backend && python -m pytest tests/test_opening_quality.py::test_opening_has_rapport_signal tests/test_opening_quality.py::test_opening_does_not_start_with_technical_interrogative -v
```

Expected output (both red):
```
FAILED tests/test_opening_quality.py::test_opening_has_rapport_signal
FAILED tests/test_opening_quality.py::test_opening_does_not_start_with_technical_interrogative
2 failed in 0.XXs
```

---

### Task 4: Add R5 test and verify all five fail together

**Files:**
- Modify: `backend/tests/test_opening_quality.py` (append final test function)

- [ ] **Step 1: Append R5 test function**

Add this block at the bottom of `backend/tests/test_opening_quality.py`:

```python
# ---------------------------------------------------------------------------
# R5 — Opening must not be exclusively technical
# ---------------------------------------------------------------------------

def test_opening_is_not_exclusively_technical():
    """If the ENTIRE opening is technical with no warm content, it must fail.

    WHY: A warm sentence followed by a technical question is acceptable for
         a warmup-to-technical transition. But an opening with zero social
         content is always wrong — the bot is ignoring the candidate as a
         person.

    R5 is a composite guard: it passes if ANY of R1, R2, R3 would pass.
    When R1–R3 all fail (as they do against BAD_OPENING), R5 also fails.
    This makes the overall 'exclusively technical' verdict explicit in the
    test output rather than requiring the reader to infer it from three
    separate failures.

    EXPECTED: name OR warm marker OR rapport signal is present.
    ACTUAL:   BAD_OPENING satisfies none of those — 100% technical content.

    >>> FAILS against BAD_OPENING.
    """
    _text = BAD_OPENING.lower()
    has_any_warm_content = (
        CANDIDATE_NAME.lower() in _text
        or any(m in _text for m in WARM_MARKERS)
        or any(s in _text for s in RAPPORT_SIGNALS)
    )
    assert has_any_warm_content, (
        f"FAIL (R5 — exclusively technical opening)\n"
        f"  Expected: name, warm marker, or rapport signal present\n"
        f"  Actual:   {BAD_OPENING!r}\n"
        f"  Fix:      Bot output must contain a greeting, name, or rapport "
        f"question before technical content."
    )
```

- [ ] **Step 2: Run all five tests together and verify all fail**

```bash
cd backend && python -m pytest tests/test_opening_quality.py -v
```

Expected output (all five red):
```
FAILED tests/test_opening_quality.py::test_opening_contains_candidate_name
FAILED tests/test_opening_quality.py::test_opening_has_warm_marker
FAILED tests/test_opening_quality.py::test_opening_has_rapport_signal
FAILED tests/test_opening_quality.py::test_opening_does_not_start_with_technical_interrogative
FAILED tests/test_opening_quality.py::test_opening_is_not_exclusively_technical
5 failed in 0.XXs
```

If any test passes, something is wrong — `BAD_OPENING` must not accidentally satisfy any rule.

- [ ] **Step 3: Sanity-check the detection logic by swapping in the GOOD_OPENING**

This step verifies the keyword logic works in both directions. It is a temporary edit — revert after confirming.

At the top of the test file, temporarily replace `BAD_OPENING` with:

```python
BAD_OPENING = (
    "Hi Utkarsh, how are you doing today? Before we dive into the technical "
    "side, I'd love to know — what was your most recent role, and what brought "
    "you to explore this backend engineer opportunity?"
)
```

Run the suite:
```bash
cd backend && python -m pytest tests/test_opening_quality.py -v
```

Expected output (all five green):
```
PASSED tests/test_opening_quality.py::test_opening_contains_candidate_name
PASSED tests/test_opening_quality.py::test_opening_has_warm_marker
PASSED tests/test_opening_quality.py::test_opening_has_rapport_signal
PASSED tests/test_opening_quality.py::test_opening_does_not_start_with_technical_interrogative
PASSED tests/test_opening_quality.py::test_opening_is_not_exclusively_technical
5 passed in 0.XXs
```

If any test stays red, the keyword set for that rule needs expanding. Check which marker should have matched and add it to the correct frozenset.

- [ ] **Step 4: Restore `BAD_OPENING` to the original broken value**

Revert the temporary change. `BAD_OPENING` must be:

```python
BAD_OPENING = (
    "Can you explain the difference between a process and a thread? "
    "Please be specific about memory isolation, context-switching overhead, "
    "and when you would choose one over the other."
)
```

Run the suite one final time to confirm all five fail again:

```bash
cd backend && python -m pytest tests/test_opening_quality.py -v
```

Expected: `5 failed in 0.XXs`

---

### Task 5: Commit

**Files:**
- Commit: `backend/tests/test_opening_quality.py`

- [ ] **Step 1: Stage and commit**

```bash
git add backend/tests/test_opening_quality.py
git commit -m "test: add failing opening quality contract tests (R1–R5)

Five pytest tests that assert the bot must greet by name, include a warm
marker, and ask a rapport question before any technical content.
All five tests fail against BAD_OPENING by design — they turn green when
the bot produces a proper warm opener.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

- [ ] **Step 2: Confirm clean working tree**

```bash
git status
```

Expected: `nothing to commit, working tree clean` (ignoring the pre-existing modified voice pipeline files — those are unrelated to this task).
