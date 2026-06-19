# JD-Driven Interview Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let admins create a reusable, JD-anchored interview config that generates a frozen, deterministic question plan (80/20 core/JD + behavioral + project deep-dive), with a resume-personalized warmup and a smooth closing outro.

**Architecture:** Configs are built once at creation (JD analyzed by LLM, core questions frozen from the bank, JD/behavioral/project questions assembled) and persisted to a new `interview_configs` SQLite table. Candidate sessions load the frozen plan into the existing deterministic questioning loop. A new forward-only `WRAP_UP` state adds a constrained candidate Q&A before evaluation.

**Tech Stack:** FastAPI, Pydantic v2, aiosqlite (SQLite), Redis (session state), Anthropic (Haiku) for JD extraction, pytest (`asyncio_mode=auto`). Next.js 14 frontend.

**Spec:** `docs/superpowers/specs/2026-06-19-jd-driven-interview-config-design.md`

**Conventions (from the existing codebase — follow exactly):**
- Tests live in `backend/tests/`, run from `backend/` with `pytest`. `pythonpath=.`, `asyncio_mode=auto`, `filterwarnings = error::RuntimeWarning`.
- Route handlers are tested by **importing and calling the function directly** (not via HTTP client).
- Redis is mocked by patching `src.lib.redis_client.set_json` / `get_json` and the **used binding** `src.services.interview.session_manager.get_question_set`.
- SQLite models mirror `backend/src/models/interview_report.py`: module-level `DB_PATH`, cached `_db`, `_get_db()`, `_init_tables()`, graceful-degrade on read.
- All shell commands below are run from `backend/`.

---

## Task 1: Add `WRAP_UP` state and forward-only transitions

**Files:**
- Modify: `backend/src/types/interview.py` (add enum member)
- Modify: `backend/src/services/interview/state_machine.py:3-10`
- Test: `backend/tests/test_state_machine_wrapup.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_state_machine_wrapup.py
"""WRAP_UP state transitions.

WHY: The interview must offer a closing candidate-Q&A phase before evaluation
instead of jumping straight to COMPLETE. WRAP_UP is forward-only — adding a
backward edge would violate the forward-only state-machine invariant.
"""
import pytest
from src.types.interview import InterviewState
from src.services.interview.state_machine import can_transition, transition


def test_questioning_can_enter_wrapup():
    assert can_transition(InterviewState.QUESTIONING, InterviewState.WRAP_UP)


def test_wrapup_self_loop_allowed():
    assert can_transition(InterviewState.WRAP_UP, InterviewState.WRAP_UP)


def test_wrapup_advances_to_evaluating():
    assert can_transition(InterviewState.WRAP_UP, InterviewState.EVALUATING)


def test_wrapup_cannot_go_back_to_questioning():
    assert not can_transition(InterviewState.WRAP_UP, InterviewState.QUESTIONING)


def test_wrapup_cannot_skip_to_complete():
    assert not can_transition(InterviewState.WRAP_UP, InterviewState.COMPLETE)


def test_transition_raises_on_backward_edge():
    with pytest.raises(ValueError):
        transition(InterviewState.WRAP_UP, InterviewState.QUESTIONING)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_state_machine_wrapup.py -v`
Expected: FAIL — `AttributeError: WRAP_UP` (enum member missing).

- [ ] **Step 3: Add the enum member**

In `backend/src/types/interview.py`, add `WRAP_UP` to `InterviewState` between `QUESTIONING` and `EVALUATING`:

```python
class InterviewState(str, Enum):
    IDLE = "idle"
    STARTED = "started"
    WARMUP = "warmup"
    QUESTIONING = "questioning"
    WRAP_UP = "wrap_up"
    EVALUATING = "evaluating"
    COMPLETE = "complete"
```

- [ ] **Step 4: Add the transitions**

Replace the `_TRANSITIONS` dict in `backend/src/services/interview/state_machine.py`:

```python
_TRANSITIONS: dict[InterviewState, set[InterviewState]] = {
    InterviewState.IDLE: {InterviewState.STARTED},
    InterviewState.STARTED: {InterviewState.WARMUP, InterviewState.QUESTIONING},
    InterviewState.WARMUP: {InterviewState.QUESTIONING},
    InterviewState.QUESTIONING: {
        InterviewState.QUESTIONING,
        InterviewState.WRAP_UP,
        InterviewState.EVALUATING,
    },
    InterviewState.WRAP_UP: {InterviewState.WRAP_UP, InterviewState.EVALUATING},
    InterviewState.EVALUATING: {InterviewState.COMPLETE},
    InterviewState.COMPLETE: set(),
}
```

(`QUESTIONING → EVALUATING` is retained so the legacy `/interview/start` flow, which has no outro, is unaffected.)

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_state_machine_wrapup.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Run the full suite to confirm no regressions**

Run: `pytest -q`
Expected: all existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/types/interview.py src/services/interview/state_machine.py tests/test_state_machine_wrapup.py
git commit -m "feat(state): add forward-only WRAP_UP state for outro phase"
```

---

## Task 2: Config types and the deterministic split function

**Files:**
- Create: `backend/src/types/config.py`
- Create: `backend/src/services/interview/plan_math.py`
- Test: `backend/tests/test_plan_math.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_plan_math.py
"""Deterministic 80/20 split math.

WHY: total_questions includes the 2 reserved slots (behavioral + project deep-dive).
The remaining technical pool is split ~80/20 core/JD, but JD is floored at 1 so a
JD-driven config ALWAYS asks at least one JD question, and core is floored at 1.
These floors — not a literal 0.8 — are the invariant we test.
"""
import pytest
from src.services.interview.plan_math import compute_split


@pytest.mark.parametrize(
    "total,ratio,expected_core,expected_jd",
    [
        (6, 0.8, 3, 1),
        (5, 0.8, 2, 1),
        (8, 0.8, 5, 1),
        (4, 0.8, 1, 1),   # floor applies: technical=2 -> jd floored to 1
        (10, 0.8, 6, 2),
    ],
)
def test_compute_split_values(total, ratio, expected_core, expected_jd):
    core, jd = compute_split(total, ratio)
    assert (core, jd) == (expected_core, expected_jd)


@pytest.mark.parametrize("total", [4, 5, 6, 7, 8, 9, 10, 15, 20])
def test_split_invariants(total):
    core, jd = compute_split(total, 0.8)
    assert core >= 1
    assert jd >= 1
    assert core + jd + 2 == total  # 2 reserved slots
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_plan_math.py -v`
Expected: FAIL — `ModuleNotFoundError: src.services.interview.plan_math`.

- [ ] **Step 3: Implement the split function**

```python
# backend/src/services/interview/plan_math.py
"""Deterministic question-count math for an interview plan.

total_questions includes 2 reserved slots (behavioral + project deep-dive).
The remaining technical pool is split into core (bank) and JD-specific questions.
"""

RESERVED_SLOTS = 2  # behavioral (disagreement) + project deep-dive


def compute_split(total_questions: int, core_ratio: float) -> tuple[int, int]:
    """Return (core_count, jd_count) for the technical pool.

    JD is floored at 1 so a JD-driven config always asks at least one JD question;
    core is therefore floored at 1 for any valid total (>= 4).
    """
    technical = total_questions - RESERVED_SLOTS
    jd_count = max(1, technical - round(technical * core_ratio))
    core_count = technical - jd_count
    return core_count, jd_count
```

- [ ] **Step 4: Create the config types**

```python
# backend/src/types/config.py
from typing import Optional
from pydantic import BaseModel, Field
from src.types.interview import ExperienceLevel, Question


class JDSummary(BaseModel):
    skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    seniority_signals: list[str] = Field(default_factory=list)


class InterviewPlan(BaseModel):
    """Frozen, ordered scored questions for a config.

    Order is fixed: [core...] -> [jd...] -> behavioral -> project_deepdive.
    warmup/outro are structural phases handled by the run flow, not entries here.
    """
    questions: list[Question] = Field(default_factory=list)
    has_warmup: bool = True
    has_outro: bool = True


class InterviewConfig(BaseModel):
    id: str
    title: str
    role: str
    experience_level: ExperienceLevel
    job_description: str
    total_questions: int
    core_question_ratio: float = 0.8
    jd_summary: JDSummary = Field(default_factory=JDSummary)
    interview_plan: InterviewPlan = Field(default_factory=InterviewPlan)
    created_at: Optional[str] = None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_plan_math.py -v`
Expected: PASS (8 passed).

- [ ] **Step 6: Commit**

```bash
git add src/types/config.py src/services/interview/plan_math.py tests/test_plan_math.py
git commit -m "feat(config): add config types and deterministic 80/20 split math"
```

---

## Task 3: SQLite persistence for configs

**Files:**
- Create: `backend/src/models/interview_config.py`
- Test: `backend/tests/test_interview_config_store.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_interview_config_store.py
"""SQLite persistence for interview configs.

WHY: Configs must persist durably and reload byte-identically (same config ->
same frozen plan). Create must fail loud (return False) when the DB is unavailable,
unlike the reports layer which degrades silently.
"""
import pytest

from src.types.config import InterviewConfig, InterviewPlan, JDSummary
from src.types.interview import ExperienceLevel, Question, QuestionType
import src.models.interview_config as store


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "test_configs.db"))
    monkeypatch.setattr(store, "_db", None)
    yield
    monkeypatch.setattr(store, "_db", None)


def _config() -> InterviewConfig:
    q = Question(
        id="c1", topic="python", difficulty="medium",
        question_type=QuestionType.CONCEPTUAL, experience_level="mid",
        question_text="Explain the GIL.", rubric={"criteria": []}, tags=["bank_core"],
    )
    return InterviewConfig(
        id="cfg-1", title="Backend Hiring", role="backend engineer",
        experience_level=ExperienceLevel.MID, job_description="We need a Python backend dev.",
        total_questions=6, core_question_ratio=0.8,
        jd_summary=JDSummary(skills=["python"], responsibilities=["APIs"], seniority_signals=["mid"]),
        interview_plan=InterviewPlan(questions=[q]),
    )


@pytest.mark.asyncio
async def test_save_then_get_roundtrip_is_identical():
    cfg = _config()
    assert await store.save_config(cfg) is True
    loaded = await store.get_config("cfg-1")
    assert loaded is not None
    assert loaded.interview_plan.questions[0].question_text == "Explain the GIL."
    assert loaded.jd_summary.skills == ["python"]
    assert loaded.total_questions == 6


@pytest.mark.asyncio
async def test_get_missing_returns_none():
    assert await store.get_config("does-not-exist") is None


@pytest.mark.asyncio
async def test_list_configs_returns_saved():
    await store.save_config(_config())
    configs = await store.list_configs()
    assert len(configs) == 1
    assert configs[0].id == "cfg-1"


@pytest.mark.asyncio
async def test_save_fails_loud_when_db_unavailable(monkeypatch):
    async def _no_db():
        return None
    monkeypatch.setattr(store, "_get_db", _no_db)
    assert await store.save_config(_config()) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_interview_config_store.py -v`
Expected: FAIL — `ModuleNotFoundError: src.models.interview_config`.

- [ ] **Step 3: Implement the store**

```python
# backend/src/models/interview_config.py
"""aiosqlite persistence for interview_configs. Direct SQL, no ORM.

Mirrors models/interview_report.py, but config CREATE fails loud (returns False
on DB failure) — a missing/half-saved config must never silently produce a broken
interview.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from src.types.config import InterviewConfig, InterviewPlan, JDSummary

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "interviews.db")

_db: Optional[aiosqlite.Connection] = None


async def _get_db() -> Optional[aiosqlite.Connection]:
    global _db
    if _db is not None:
        try:
            await _db.execute("SELECT 1")
            return _db
        except Exception:
            _db = None
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _init_tables(_db)
        return _db
    except Exception as exc:
        logger.error("Failed to open SQLite DB at %s: %s", DB_PATH, exc)
        return None


async def _init_tables(db: aiosqlite.Connection) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS interview_configs (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            role TEXT NOT NULL,
            experience_level TEXT NOT NULL,
            job_description TEXT NOT NULL,
            total_questions INTEGER NOT NULL,
            core_question_ratio REAL NOT NULL DEFAULT 0.8,
            jd_summary TEXT NOT NULL DEFAULT '{}',
            interview_plan TEXT NOT NULL DEFAULT '{}',
            created_at TEXT
        )
    """)
    await db.commit()


async def save_config(config: InterviewConfig) -> bool:
    db = await _get_db()
    if db is None:
        logger.error("No DB — config not saved id=%s", config.id)
        return False
    now = datetime.now(timezone.utc).isoformat()
    try:
        await db.execute(
            """
            INSERT INTO interview_configs
                (id, title, role, experience_level, job_description, total_questions,
                 core_question_ratio, jd_summary, interview_plan, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                role = excluded.role,
                experience_level = excluded.experience_level,
                job_description = excluded.job_description,
                total_questions = excluded.total_questions,
                core_question_ratio = excluded.core_question_ratio,
                jd_summary = excluded.jd_summary,
                interview_plan = excluded.interview_plan
            """,
            (
                config.id, config.title, config.role, config.experience_level.value,
                config.job_description, config.total_questions, config.core_question_ratio,
                config.jd_summary.model_dump_json(), config.interview_plan.model_dump_json(),
                now,
            ),
        )
        await db.commit()
        logger.info("Config saved id=%s", config.id)
        return True
    except Exception as exc:
        logger.error("Failed to save config id=%s: %s", config.id, exc)
        return False


def _row_to_config(row) -> InterviewConfig:
    from src.types.interview import ExperienceLevel
    return InterviewConfig(
        id=row["id"],
        title=row["title"],
        role=row["role"],
        experience_level=ExperienceLevel(row["experience_level"]),
        job_description=row["job_description"],
        total_questions=row["total_questions"],
        core_question_ratio=row["core_question_ratio"],
        jd_summary=JDSummary.model_validate_json(row["jd_summary"]) if row["jd_summary"] else JDSummary(),
        interview_plan=InterviewPlan.model_validate_json(row["interview_plan"]) if row["interview_plan"] else InterviewPlan(),
        created_at=row["created_at"],
    )


async def get_config(config_id: str) -> Optional[InterviewConfig]:
    db = await _get_db()
    if db is None:
        return None
    try:
        cursor = await db.execute(
            "SELECT * FROM interview_configs WHERE id = ?", (config_id,)
        )
        row = await cursor.fetchone()
        return _row_to_config(row) if row is not None else None
    except Exception as exc:
        logger.error("Failed to read config id=%s: %s", config_id, exc)
        return None


async def list_configs(limit: int = 50, offset: int = 0) -> list[InterviewConfig]:
    db = await _get_db()
    if db is None:
        return []
    try:
        cursor = await db.execute(
            "SELECT * FROM interview_configs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = await cursor.fetchall()
        out: list[InterviewConfig] = []
        for row in rows:
            try:
                out.append(_row_to_config(row))
            except Exception as exc:
                logger.warning("Skipping malformed config row: %s", exc)
        return out
    except Exception as exc:
        logger.error("Failed to list configs: %s", exc)
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_interview_config_store.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/models/interview_config.py tests/test_interview_config_store.py
git commit -m "feat(config): add SQLite persistence for interview configs (fail-loud create)"
```

---

## Task 4: Deterministic special questions (behavioral, project, JD rubric)

**Files:**
- Create: `backend/src/services/interview/special_questions.py`
- Test: `backend/tests/test_special_questions.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_special_questions.py
"""Fixed, deterministic special questions.

WHY: The behavioral (disagreement) and project deep-dive questions must be
deterministic (no LLM) so the same config yields the same plan. JD questions are
LLM-sourced but need a usable rubric so the existing evaluator path works unchanged.
None may probe family or protected-class topics.
"""
from src.services.interview.special_questions import (
    build_behavioral_question,
    build_project_question,
    build_jd_question,
)

PROTECTED = {"family", "married", "children", "religion", "age", "nationality", "gender"}


def test_behavioral_is_about_disagreement():
    q = build_behavioral_question()
    assert "disagree" in q.question_text.lower()
    assert q.tags == ["behavioral"]
    assert q.rubric  # non-empty rubric


def test_project_is_deep_dive():
    q = build_project_question()
    assert "project" in q.question_text.lower()
    assert q.tags == ["project_deepdive"]
    assert q.rubric


def test_special_questions_are_deterministic():
    assert build_behavioral_question().question_text == build_behavioral_question().question_text
    assert build_project_question().question_text == build_project_question().question_text


def test_special_questions_avoid_protected_topics():
    for q in (build_behavioral_question(), build_project_question()):
        text = q.question_text.lower()
        for word in PROTECTED:
            assert word not in text


def test_build_jd_question_has_rubric_and_tag():
    q = build_jd_question("How would you design a rate limiter?", "rate limiting", index=0)
    assert q.id == "jd_0"
    assert q.question_text == "How would you design a rate limiter?"
    assert q.topic == "rate limiting"
    assert q.tags == ["jd_generated"]
    assert q.rubric
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_special_questions.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# backend/src/services/interview/special_questions.py
"""Deterministic question builders for the non-bank plan slots.

Behavioral + project questions are fixed templates (no LLM). JD questions are
LLM-sourced text wrapped into the Question model with a generic competency rubric
so the existing evaluator works unchanged.
"""
from src.types.interview import Question, QuestionType

_GENERIC_RUBRIC = {
    "criteria": [
        "Clarity and structure of the answer",
        "Concrete, specific examples over generalities",
        "Depth of reasoning and trade-off awareness",
    ]
}

_BEHAVIORAL_TEXT = (
    "Tell me about a time you disagreed with a colleague on a technical decision. "
    "How did you handle it, and what was the outcome?"
)

_PROJECT_TEXT = (
    "Walk me through a recent project you're proud of — what problem it solved, "
    "the key technical decisions you made, and what you'd do differently now."
)


def build_behavioral_question() -> Question:
    return Question(
        id="behavioral_0",
        topic="collaboration",
        difficulty="medium",
        question_type=QuestionType.BEHAVIORAL,
        experience_level="all",
        question_text=_BEHAVIORAL_TEXT,
        rubric=_GENERIC_RUBRIC,
        tags=["behavioral"],
    )


def build_project_question() -> Question:
    return Question(
        id="project_0",
        topic="project deep-dive",
        difficulty="medium",
        question_type=QuestionType.SCENARIO,
        experience_level="all",
        question_text=_PROJECT_TEXT,
        rubric=_GENERIC_RUBRIC,
        tags=["project_deepdive"],
    )


def build_jd_question(question_text: str, topic: str, index: int) -> Question:
    return Question(
        id=f"jd_{index}",
        topic=topic or "role-specific",
        difficulty="medium",
        question_type=QuestionType.CONCEPTUAL,
        experience_level="all",
        question_text=question_text,
        rubric=_GENERIC_RUBRIC,
        tags=["jd_generated"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_special_questions.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/services/interview/special_questions.py tests/test_special_questions.py
git commit -m "feat(config): add deterministic behavioral/project/JD question builders"
```

---

## Task 5: JD analysis LLM service (fail-loud)

**Files:**
- Create: `backend/src/prompts/jd_analysis_prompt.txt`
- Create: `backend/src/services/llm/jd_analysis.py`
- Modify: `backend/src/lib/anthropic_client.py:27-34` (add `jd_analysis` task model)
- Test: `backend/tests/test_jd_analysis.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_jd_analysis.py
"""JD analysis extraction (LLM).

WHY: JD analysis is extraction (allowed LLM use). It must parse the model's JSON
into a JDSummary + JD question ideas, and FAIL LOUD (raise) on LLM/parse failure so
config creation does not persist a half-built config.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.services.llm.jd_analysis import analyze_jd, JDAnalysisError


def _mock_response(text: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


VALID_JSON = """
{
  "skills": ["python", "fastapi"],
  "responsibilities": ["build APIs"],
  "seniority_signals": ["mid-level ownership"],
  "jd_questions": [
    {"question_text": "How do you design a rate limiter?", "topic": "rate limiting"},
    {"question_text": "Explain dependency injection in FastAPI.", "topic": "fastapi"}
  ]
}
"""


def test_analyze_jd_parses_summary_and_questions():
    client = MagicMock()
    client.messages.create.return_value = _mock_response(VALID_JSON)
    with patch("src.services.llm.jd_analysis.get_anthropic_client", return_value=client):
        summary, questions = analyze_jd("We need a Python/FastAPI backend engineer.")
    assert summary.skills == ["python", "fastapi"]
    assert summary.responsibilities == ["build APIs"]
    assert len(questions) == 2
    assert questions[0]["question_text"].startswith("How do you design")


def test_analyze_jd_raises_on_malformed_output():
    client = MagicMock()
    client.messages.create.return_value = _mock_response("not json at all")
    with patch("src.services.llm.jd_analysis.get_anthropic_client", return_value=client):
        with pytest.raises(JDAnalysisError):
            analyze_jd("some jd")


def test_analyze_jd_raises_on_client_error():
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("API down")
    with patch("src.services.llm.jd_analysis.get_anthropic_client", return_value=client):
        with pytest.raises(JDAnalysisError):
            analyze_jd("some jd")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_jd_analysis.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Add the prompt file**

```
# backend/src/prompts/jd_analysis_prompt.txt
You are analyzing a job description to prepare a technical interview.

Extract the following and return ONLY a single valid JSON object, no prose:

{
  "skills": ["up to 8 concrete technical skills/technologies named or strongly implied"],
  "responsibilities": ["3-6 core responsibilities"],
  "seniority_signals": ["phrases indicating seniority/scope expectations"],
  "jd_questions": [
    {"question_text": "a role-specific technical question grounded in THIS job description", "topic": "short topic label"}
  ]
}

Rules:
- Produce at least {min_questions} entries in "jd_questions".
- Questions must be technical and specific to the job description.
- Never ask about family, age, gender, nationality, religion, or other personal/protected-class topics.
- Return ONLY the JSON object.

Job description:
{job_description}
```

- [ ] **Step 4: Add the task model mapping**

In `backend/src/lib/anthropic_client.py`, add `jd_analysis` to the `models` dict in `get_model_for_task`:

```python
def get_model_for_task(task: str) -> str:
    models = {
        "interview": "claude-haiku-4-5-20251001",
        "evaluation": "claude-haiku-4-5-20251001",
        "follow_up": "claude-haiku-4-5-20251001",
        "compression": "claude-haiku-4-5-20251001",
        "jd_analysis": "claude-haiku-4-5-20251001",
    }
    return models.get(task, "claude-haiku-4-5-20251001")
```

- [ ] **Step 5: Implement the service**

```python
# backend/src/services/llm/jd_analysis.py
"""JD analysis via LLM (extraction). Fails loud on error.

Returns (JDSummary, list of JD question dicts). Raises JDAnalysisError on any
LLM or parse failure so the caller can refuse to persist a half-built config.
"""
import json
import logging
import os

from src.lib.anthropic_client import get_anthropic_client, get_model_for_task
from src.types.config import JDSummary

logger = logging.getLogger(__name__)

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "prompts", "jd_analysis_prompt.txt")


class JDAnalysisError(RuntimeError):
    """Raised when JD analysis cannot produce a usable result."""


def analyze_jd(job_description: str, min_questions: int = 3) -> tuple[JDSummary, list[dict]]:
    with open(_PROMPT_PATH, encoding="utf-8") as f:
        template = f.read()
    prompt = template.replace("{min_questions}", str(min_questions)).replace(
        "{job_description}", job_description
    )

    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=get_model_for_task("jd_analysis"),
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
    except Exception as exc:
        logger.error("JD analysis LLM call failed: %s", exc)
        raise JDAnalysisError(f"JD analysis LLM call failed: {exc}") from exc

    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise JDAnalysisError("JD analysis returned no JSON object")
    try:
        data = json.loads(raw[start:end])
    except json.JSONDecodeError as exc:
        raise JDAnalysisError(f"JD analysis returned invalid JSON: {exc}") from exc

    summary = JDSummary(
        skills=[str(s) for s in data.get("skills", [])][:8],
        responsibilities=[str(r) for r in data.get("responsibilities", [])],
        seniority_signals=[str(s) for s in data.get("seniority_signals", [])],
    )
    questions = [
        {"question_text": str(q.get("question_text", "")).strip(),
         "topic": str(q.get("topic", "")).strip()}
        for q in data.get("jd_questions", [])
        if str(q.get("question_text", "")).strip()
    ]
    if not questions:
        raise JDAnalysisError("JD analysis produced no usable questions")
    return summary, questions
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_jd_analysis.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Commit**

```bash
git add src/prompts/jd_analysis_prompt.txt src/services/llm/jd_analysis.py src/lib/anthropic_client.py tests/test_jd_analysis.py
git commit -m "feat(config): add fail-loud JD analysis extraction service"
```

---

## Task 6: Plan builder (assemble the frozen plan)

**Files:**
- Create: `backend/src/services/interview/plan_builder.py`
- Test: `backend/tests/test_plan_builder.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_plan_builder.py
"""Frozen plan assembly.

WHY: The plan must be deterministic given fixed inputs, honor the 80/20 split with
floors, place the 2 special questions last in fixed order, and fail loud if the bank
cannot supply enough core questions.
"""
from unittest.mock import patch

import pytest

from src.services.interview.plan_builder import build_plan, InsufficientQuestionsError
from src.types.config import JDSummary
from src.types.interview import ExperienceLevel, Question, QuestionType


def _bank_q(qid: str) -> Question:
    return Question(
        id=qid, topic=f"topic_{qid}", difficulty="medium",
        question_type=QuestionType.CONCEPTUAL, experience_level="mid",
        question_text=f"Bank question {qid}", rubric={"criteria": []}, tags=["core"],
    )


_JD_IDEAS = [
    {"question_text": "JD Q1", "topic": "t1"},
    {"question_text": "JD Q2", "topic": "t2"},
    {"question_text": "JD Q3", "topic": "t3"},
]
_SUMMARY = JDSummary(skills=["python"], responsibilities=["x"], seniority_signals=["mid"])


def _build(total=6):
    # get_question_set returns exactly `count` bank questions
    def fake_get_question_set(role, level, skills, count):
        return [_bank_q(f"c{i}") for i in range(count)]

    with patch("src.services.interview.plan_builder.get_question_set", side_effect=fake_get_question_set):
        return build_plan(
            role="backend engineer", experience_level=ExperienceLevel.MID,
            jd_summary=_SUMMARY, jd_question_ideas=_JD_IDEAS,
            total_questions=total, core_ratio=0.8,
        )


def test_plan_question_count_matches_total():
    plan = _build(total=6)
    assert len(plan.questions) == 6


def test_plan_order_core_jd_behavioral_project():
    plan = _build(total=6)  # core=3, jd=1, +behavioral +project
    tags = [q.tags[0] for q in plan.questions]
    assert tags == ["core", "core", "core", "jd_generated", "behavioral", "project_deepdive"]


def test_plan_is_deterministic():
    a = _build(total=6)
    b = _build(total=6)
    assert [q.question_text for q in a.questions] == [q.question_text for q in b.questions]


def test_plan_fails_loud_on_insufficient_bank_questions():
    def short_bank(role, level, skills, count):
        return [_bank_q("c0")]  # only 1, fewer than requested
    with patch("src.services.interview.plan_builder.get_question_set", side_effect=short_bank):
        with pytest.raises(InsufficientQuestionsError):
            build_plan(
                role="r", experience_level=ExperienceLevel.MID, jd_summary=_SUMMARY,
                jd_question_ideas=_JD_IDEAS, total_questions=6, core_ratio=0.8,
            )


def test_plan_fails_loud_on_insufficient_jd_ideas():
    def fake_get_question_set(role, level, skills, count):
        return [_bank_q(f"c{i}") for i in range(count)]
    with patch("src.services.interview.plan_builder.get_question_set", side_effect=fake_get_question_set):
        with pytest.raises(InsufficientQuestionsError):
            build_plan(
                role="r", experience_level=ExperienceLevel.MID, jd_summary=_SUMMARY,
                jd_question_ideas=[], total_questions=6, core_ratio=0.8,
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_plan_builder.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# backend/src/services/interview/plan_builder.py
"""Assemble a frozen, deterministic InterviewPlan from config inputs.

Order: [core...] -> [jd...] -> behavioral -> project_deepdive.
Fails loud if the bank cannot supply the required core count or there are not
enough JD question ideas.
"""
from src.services.interview.plan_math import compute_split
from src.services.interview.special_questions import (
    build_behavioral_question,
    build_jd_question,
    build_project_question,
)
from src.services.questions.question_bank import get_question_set
from src.types.config import InterviewPlan, JDSummary
from src.types.interview import ExperienceLevel


class InsufficientQuestionsError(RuntimeError):
    """Raised when the plan cannot be fully populated."""


def build_plan(
    role: str,
    experience_level: ExperienceLevel,
    jd_summary: JDSummary,
    jd_question_ideas: list[dict],
    total_questions: int,
    core_ratio: float,
) -> InterviewPlan:
    core_count, jd_count = compute_split(total_questions, core_ratio)

    core_qs = get_question_set(role, experience_level, jd_summary.skills, core_count)
    if len(core_qs) < core_count:
        raise InsufficientQuestionsError(
            f"Bank supplied {len(core_qs)} core questions, need {core_count}"
        )
    core_qs = core_qs[:core_count]

    if len(jd_question_ideas) < jd_count:
        raise InsufficientQuestionsError(
            f"Have {len(jd_question_ideas)} JD ideas, need {jd_count}"
        )
    jd_qs = [
        build_jd_question(idea["question_text"], idea.get("topic", ""), index=i)
        for i, idea in enumerate(jd_question_ideas[:jd_count])
    ]

    questions = core_qs + jd_qs + [build_behavioral_question(), build_project_question()]
    return InterviewPlan(questions=questions)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_plan_builder.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/services/interview/plan_builder.py tests/test_plan_builder.py
git commit -m "feat(config): assemble frozen deterministic interview plan"
```

---

## Task 7: Config creation endpoint `POST /admin/configs`

**Files:**
- Modify: `backend/src/types/admin.py` (add `CreateConfigRequest`, `ConfigResponse`)
- Modify: `backend/src/routes/admin.py` (add create route)
- Test: `backend/tests/test_admin_create_config.py` (create)

- [ ] **Step 1: Add request/response types**

Append to `backend/src/types/admin.py`:

```python
from pydantic import field_validator
from .interview import ExperienceLevel


class CreateConfigRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=100)
    experience_level: ExperienceLevel
    job_description: str = Field(min_length=1, max_length=20000)
    total_questions: int = Field(ge=4, le=20)
    core_question_ratio: float = Field(default=0.8, gt=0, lt=1)

    @field_validator("job_description")
    def jd_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("job_description must not be blank")
        return v


class ConfigResponse(BaseModel):
    id: str
    title: str
    role: str
    experience_level: str
    total_questions: int
    core_question_ratio: float
    created_at: Optional[str] = None
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_admin_create_config.py
"""POST /admin/configs.

WHY: JD is mandatory; invalid configs are rejected before any persistence; JD-analysis
failure and DB-write failure both surface as loud HTTP errors (no half-built config).
"""
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.types.admin import CreateConfigRequest
from src.types.config import JDSummary
from src.types.interview import ExperienceLevel


def _req(**over):
    base = dict(
        title="Backend Hiring", role="backend engineer",
        experience_level=ExperienceLevel.MID,
        job_description="We need a Python/FastAPI engineer to build APIs.",
        total_questions=6, core_question_ratio=0.8,
    )
    base.update(over)
    return CreateConfigRequest(**base)


def test_blank_jd_rejected_by_model():
    with pytest.raises(ValidationError):
        _req(job_description="   ")


def test_total_below_minimum_rejected_by_model():
    with pytest.raises(ValidationError):
        _req(total_questions=3)


def test_ratio_out_of_range_rejected_by_model():
    with pytest.raises(ValidationError):
        _req(core_question_ratio=1.0)


@pytest.mark.asyncio
async def test_create_config_happy_path_persists():
    from src.routes.admin import create_config
    summary = JDSummary(skills=["python"], responsibilities=["apis"], seniority_signals=["mid"])
    ideas = [{"question_text": "Q1", "topic": "t1"}, {"question_text": "Q2", "topic": "t2"}]
    with (
        patch("src.routes.admin.analyze_jd", return_value=(summary, ideas)),
        patch("src.routes.admin.save_config", new=AsyncMock(return_value=True)) as save,
    ):
        resp = await create_config(_req())
    assert resp.total_questions == 6
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_jd_analysis_failure_returns_502_and_does_not_persist():
    from src.routes.admin import create_config
    from src.services.llm.jd_analysis import JDAnalysisError
    with (
        patch("src.routes.admin.analyze_jd", side_effect=JDAnalysisError("boom")),
        patch("src.routes.admin.save_config", new=AsyncMock(return_value=True)) as save,
    ):
        with pytest.raises(HTTPException) as exc:
            await create_config(_req())
    assert exc.value.status_code == 502
    save.assert_not_called()


@pytest.mark.asyncio
async def test_insufficient_bank_questions_returns_422():
    from src.routes.admin import create_config
    from src.services.interview.plan_builder import InsufficientQuestionsError
    summary = JDSummary(skills=["python"])
    ideas = [{"question_text": "Q1", "topic": "t1"}]
    with (
        patch("src.routes.admin.analyze_jd", return_value=(summary, ideas)),
        patch("src.routes.admin.build_plan", side_effect=InsufficientQuestionsError("not enough")),
        patch("src.routes.admin.save_config", new=AsyncMock(return_value=True)) as save,
    ):
        with pytest.raises(HTTPException) as exc:
            await create_config(_req())
    assert exc.value.status_code == 422
    save.assert_not_called()


@pytest.mark.asyncio
async def test_db_write_failure_returns_500():
    from src.routes.admin import create_config
    summary = JDSummary(skills=["python"])
    ideas = [{"question_text": "Q1", "topic": "t1"}, {"question_text": "Q2", "topic": "t2"}]
    with (
        patch("src.routes.admin.analyze_jd", return_value=(summary, ideas)),
        patch("src.routes.admin.save_config", new=AsyncMock(return_value=False)),
    ):
        with pytest.raises(HTTPException) as exc:
            await create_config(_req())
    assert exc.value.status_code == 500
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_admin_create_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_config'`.

- [ ] **Step 4: Implement the route**

Add imports near the top of `backend/src/routes/admin.py`:

```python
import uuid
from src.types.admin import CreateConfigRequest, ConfigResponse
from src.types.config import InterviewConfig
from src.services.llm.jd_analysis import analyze_jd, JDAnalysisError
from src.services.interview.plan_builder import build_plan, InsufficientQuestionsError
from src.models.interview_config import save_config, get_config, list_configs as list_configs_store
```

Add the route (after `require_admin`):

```python
@router.post(
    "/configs",
    response_model=ConfigResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_config(body: CreateConfigRequest) -> ConfigResponse:
    # JD analysis (LLM, extraction). Fail loud — do NOT persist a half-built config.
    try:
        jd_summary, jd_ideas = analyze_jd(body.job_description)
    except JDAnalysisError as exc:
        logger.error("JD analysis failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Could not analyze the job description. Try again.")

    # Build the frozen plan deterministically.
    try:
        plan = build_plan(
            role=body.role,
            experience_level=body.experience_level,
            jd_summary=jd_summary,
            jd_question_ideas=jd_ideas,
            total_questions=body.total_questions,
            core_ratio=body.core_question_ratio,
        )
    except InsufficientQuestionsError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    config = InterviewConfig(
        id=str(uuid.uuid4()),
        title=body.title,
        role=body.role,
        experience_level=body.experience_level,
        job_description=body.job_description,
        total_questions=body.total_questions,
        core_question_ratio=body.core_question_ratio,
        jd_summary=jd_summary,
        interview_plan=plan,
    )

    if not await save_config(config):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Failed to persist the interview config.")

    return ConfigResponse(
        id=config.id, title=config.title, role=config.role,
        experience_level=config.experience_level.value,
        total_questions=config.total_questions,
        core_question_ratio=config.core_question_ratio,
        created_at=config.created_at,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_admin_create_config.py -v`
Expected: PASS (8 passed).

- [ ] **Step 6: Commit**

```bash
git add src/types/admin.py src/routes/admin.py tests/test_admin_create_config.py
git commit -m "feat(config): add POST /admin/configs with fail-loud validation"
```

---

## Task 8: List configs endpoint `GET /admin/configs`

**Files:**
- Modify: `backend/src/types/admin.py` (add `ConfigListResponse`)
- Modify: `backend/src/routes/admin.py` (add list route)
- Test: `backend/tests/test_admin_list_configs.py` (create)

- [ ] **Step 1: Add the response type**

Append to `backend/src/types/admin.py`:

```python
class ConfigListResponse(BaseModel):
    configs: list[ConfigResponse]
    total: int
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_admin_list_configs.py
"""GET /admin/configs returns saved configs as summaries."""
from unittest.mock import patch

import pytest

from src.types.config import InterviewConfig, JDSummary, InterviewPlan
from src.types.interview import ExperienceLevel


def _cfg(cid: str) -> InterviewConfig:
    return InterviewConfig(
        id=cid, title="t", role="backend", experience_level=ExperienceLevel.MID,
        job_description="jd", total_questions=6, core_question_ratio=0.8,
        jd_summary=JDSummary(), interview_plan=InterviewPlan(), created_at="2026-06-19T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_list_configs_returns_summaries():
    from src.routes.admin import list_configs
    with patch("src.routes.admin.list_configs_store", return_value=[_cfg("a"), _cfg("b")]):
        resp = await list_configs()
    assert resp.total == 2
    assert {c.id for c in resp.configs} == {"a", "b"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_admin_list_configs.py -v`
Expected: FAIL — `ImportError: cannot import name 'list_configs'`.

- [ ] **Step 4: Implement the route**

Add to `backend/src/routes/admin.py` (update the import line from Task 7 to also bring `ConfigListResponse`):

```python
from src.types.admin import CreateConfigRequest, ConfigResponse, ConfigListResponse
```

```python
@router.get(
    "/configs",
    response_model=ConfigListResponse,
    dependencies=[Depends(require_admin)],
)
async def list_configs() -> ConfigListResponse:
    configs = await list_configs_store()
    summaries = [
        ConfigResponse(
            id=c.id, title=c.title, role=c.role,
            experience_level=c.experience_level.value,
            total_questions=c.total_questions,
            core_question_ratio=c.core_question_ratio,
            created_at=c.created_at,
        )
        for c in configs
    ]
    return ConfigListResponse(configs=summaries, total=len(summaries))
```

Note: `list_configs_store` is the alias imported in Task 7 (`list_configs as list_configs_store`) — this avoids a name clash with the route function `list_configs`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_admin_list_configs.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Commit**

```bash
git add src/types/admin.py src/routes/admin.py tests/test_admin_list_configs.py
git commit -m "feat(config): add GET /admin/configs listing"
```

---

## Task 9: SessionState fields + resume warmup personalization

**Files:**
- Modify: `backend/src/types/interview.py` (SessionState additive fields)
- Modify: `backend/src/services/interview/warmup.py` (add `personalize_warmup`)
- Test: `backend/tests/test_resume_personalization.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_resume_personalization.py
"""Resume-personalized warmup.

WHY: Only whitelisted professional fields (skills, current_company) may shape the
warmup. PII (phone/email/linkedin/location/country_code) must NEVER appear. Missing
resume details must fall back to a generic, still-personalized-by-name warmup.
"""
from src.services.interview.warmup import personalize_warmup

PII = {
    "555", "1234567890", "@", "linkedin.com", "Berlin", "+1", "+91",
}


def test_uses_company_and_skill_when_present():
    line = personalize_warmup(
        candidate_name="Alice", job_role="backend engineer",
        details={"skills": ["Kubernetes", "Go"], "current_company": "Acme"},
    )
    assert "Acme" in line
    assert "Kubernetes" in line
    assert "Alice" in line


def test_never_leaks_pii():
    line = personalize_warmup(
        candidate_name="Alice", job_role="backend engineer",
        details={
            "skills": ["Kubernetes"], "current_company": "Acme",
            "email": "alice@x.com", "phone": "5551234567",
            "linkedin_url": "https://linkedin.com/in/alice",
            "current_location": "Berlin", "country_code": "+1",
        },
    )
    for token in PII:
        assert token not in line


def test_falls_back_to_generic_when_no_details():
    line = personalize_warmup(candidate_name="Alice", job_role="backend engineer", details=None)
    assert "Alice" in line
    assert isinstance(line, str) and len(line) > 0


def test_falls_back_when_company_missing():
    line = personalize_warmup(
        candidate_name="Alice", job_role="backend engineer",
        details={"skills": ["Rust"]},  # no current_company
    )
    assert "Alice" in line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_resume_personalization.py -v`
Expected: FAIL — `ImportError: cannot import name 'personalize_warmup'`.

- [ ] **Step 3: Implement `personalize_warmup`**

Append to `backend/src/services/interview/warmup.py`:

```python
from typing import Optional

# Only these resume fields are ever read into warmup text. Everything else
# (phone, email, linkedin_url, current_location, country_code) is PII and is
# never touched — enforced here and asserted in tests.
_WARMUP_WHITELIST = ("skills", "current_company")


def personalize_warmup(
    candidate_name: str,
    job_role: str,
    details: Optional[dict],
) -> str:
    """Build one warmup line from whitelisted resume fields, or fall back generic.

    No LLM. Reads ONLY `skills` and `current_company`.
    """
    if not details:
        return generate_warmup_question(candidate_name, job_role)

    company = (details.get("current_company") or "").strip()
    skills = details.get("skills") or []
    top_skill = (skills[0].strip() if skills and isinstance(skills[0], str) else "")

    if company and top_skill:
        return (
            f"Hi {candidate_name}! Before we dig in — I see you've been at {company} "
            f"working with {top_skill}. What have you most enjoyed building there?"
        )
    if company:
        return (
            f"Hi {candidate_name}! Before we dig in — I see you've been at {company}. "
            f"What have you most enjoyed working on there?"
        )
    # No usable whitelisted field → generic, still name-personalized.
    return generate_warmup_question(candidate_name, job_role)
```

- [ ] **Step 4: Add SessionState fields**

In `backend/src/types/interview.py`, add three additive fields to `SessionState` (after `evaluation`):

```python
    interview_config_id: Optional[str] = None
    jd_summary: Optional[dict] = None
    resume_details: Optional[dict] = None  # whitelisted subset only (skills, current_company)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_resume_personalization.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Run full suite (SessionState change is additive — confirm no regressions)**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/types/interview.py src/services/interview/warmup.py tests/test_resume_personalization.py
git commit -m "feat(config): add resume warmup personalization (whitelisted, no PII) + session fields"
```

---

## Task 10: Config-based start endpoint `POST /interview/start-from-config`

**Files:**
- Modify: `backend/src/services/interview/session_manager.py` (add `create_session_from_config`)
- Modify: `backend/src/types/api.py` (add `StartFromConfigRequest`)
- Modify: `backend/src/routes/interview.py` (add route)
- Test: `backend/tests/test_start_from_config.py` (create)

- [ ] **Step 1: Add the session factory**

Append to `backend/src/services/interview/session_manager.py`:

```python
from src.types.config import InterviewConfig


def create_session_from_config(
    config: InterviewConfig,
    candidate_name: str,
    resume_details: Optional[dict] = None,
) -> SessionState:
    """Create a session whose questions ARE the config's frozen plan.

    Stores only whitelisted resume fields (skills, current_company) — never PII.
    """
    session_id = str(uuid.uuid4())
    whitelisted = None
    if resume_details:
        whitelisted = {
            "skills": resume_details.get("skills") or [],
            "current_company": resume_details.get("current_company") or "",
        }

    session = SessionState(
        session_id=session_id,
        state=InterviewState.STARTED,
        candidate_name=candidate_name,
        job_role=config.role,
        experience_level=config.experience_level,
        required_skills=config.jd_summary.skills,
        questions=list(config.interview_plan.questions),
        current_question_idx=0,
        started_at=datetime.now(timezone.utc).isoformat(),
        interview_config_id=config.id,
        jd_summary=config.jd_summary.model_dump(),
        resume_details=whitelisted,
    )
    _persist(session)
    return session
```

- [ ] **Step 2: Add the request type**

Append to `backend/src/types/api.py`:

```python
class StartFromConfigRequest(BaseModel):
    interview_config_id: str = Field(min_length=1)
    candidate_name: str = Field(min_length=1, max_length=100)
    resume_details: Optional[dict] = None  # external parser's UserDetails (whitelisted on store)
```

- [ ] **Step 3: Write the failing test**

```python
# backend/tests/test_start_from_config.py
"""POST /interview/start-from-config.

WHY: A candidate starts from a frozen config — the session's questions must BE the
config's plan (deterministic), the warmup is personalized from whitelisted resume
fields, and a missing config returns 404.
"""
from typing import Any
from unittest.mock import patch

import pytest

from src.types.api import StartFromConfigRequest
from src.types.config import InterviewConfig, InterviewPlan, JDSummary
from src.types.interview import ExperienceLevel, InterviewState, Question, QuestionType

_STORED: dict[str, Any] = {}


def _set_json(key, value, ttl=0): _STORED[key] = value
def _get_json(key): return _STORED.get(key)


@pytest.fixture(autouse=True)
def reset_store():
    _STORED.clear()
    yield
    _STORED.clear()


def _plan_q(qid: str) -> Question:
    return Question(
        id=qid, topic=f"t_{qid}", difficulty="medium",
        question_type=QuestionType.CONCEPTUAL, experience_level="mid",
        question_text=f"Plan question {qid}", rubric={"criteria": []}, tags=["core"],
    )


def _config() -> InterviewConfig:
    return InterviewConfig(
        id="cfg-1", title="t", role="backend engineer", experience_level=ExperienceLevel.MID,
        job_description="jd", total_questions=6, core_question_ratio=0.8,
        jd_summary=JDSummary(skills=["python"]),
        interview_plan=InterviewPlan(questions=[_plan_q("a"), _plan_q("b")]),
    )


@pytest.mark.asyncio
async def test_start_from_config_uses_frozen_plan():
    from src.routes.interview import start_from_config
    with (
        patch("src.lib.redis_client.set_json", side_effect=_set_json),
        patch("src.lib.redis_client.get_json", side_effect=_get_json),
        patch("src.routes.interview.get_config", return_value=_config()),
    ):
        req = StartFromConfigRequest(interview_config_id="cfg-1", candidate_name="Alice")
        resp = await start_from_config(req)
    assert resp.state == InterviewState.WARMUP
    assert resp.total_questions == 2  # the frozen plan's question count


@pytest.mark.asyncio
async def test_start_from_config_personalizes_warmup_from_resume():
    from src.routes.interview import start_from_config
    with (
        patch("src.lib.redis_client.set_json", side_effect=_set_json),
        patch("src.lib.redis_client.get_json", side_effect=_get_json),
        patch("src.routes.interview.get_config", return_value=_config()),
    ):
        req = StartFromConfigRequest(
            interview_config_id="cfg-1", candidate_name="Alice",
            resume_details={"skills": ["Kubernetes"], "current_company": "Acme",
                            "email": "alice@x.com"},
        )
        resp = await start_from_config(req)
    assert "Acme" in resp.question_text
    assert "alice@x.com" not in resp.question_text  # PII never leaks


@pytest.mark.asyncio
async def test_start_from_config_missing_returns_404():
    from fastapi import HTTPException
    from src.routes.interview import start_from_config
    with (
        patch("src.lib.redis_client.set_json", side_effect=_set_json),
        patch("src.lib.redis_client.get_json", side_effect=_get_json),
        patch("src.routes.interview.get_config", return_value=None),
    ):
        req = StartFromConfigRequest(interview_config_id="nope", candidate_name="Alice")
        with pytest.raises(HTTPException) as exc:
            await start_from_config(req)
    assert exc.value.status_code == 404
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_start_from_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'start_from_config'`.

- [ ] **Step 5: Implement the route**

Add imports to `backend/src/routes/interview.py`:

```python
from src.types.api import StartFromConfigRequest
from src.services.interview.warmup import personalize_warmup
from src.models.interview_config import get_config
```

Add the route after `start_interview`:

```python
@router.post("/start-from-config", response_model=StartInterviewResponse, status_code=status.HTTP_201_CREATED)
async def start_from_config(body: StartFromConfigRequest) -> StartInterviewResponse:
    config = await get_config(body.interview_config_id)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interview config not found.")

    session = session_manager.create_session_from_config(
        config=config,
        candidate_name=body.candidate_name,
        resume_details=body.resume_details,
    )

    if not session.questions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Config has no questions in its plan.",
        )

    session.state = InterviewState.WARMUP
    intro = generate_introduction(session.candidate_name, session.job_role, len(session.questions))
    warmup_text = personalize_warmup(session.candidate_name, session.job_role, session.resume_details)
    opening = f"{intro} {warmup_text}"
    session_manager.update_session(session)
    session_manager.record_turn(session, speaker="bot", text=opening)

    return StartInterviewResponse(
        session_id=session.session_id,
        state=session.state,
        question_text=opening,
        question_number=0,
        total_questions=len(session.questions),
        topic="warmup",
        candidate_name=session.candidate_name,
        is_warmup=True,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_start_from_config.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Commit**

```bash
git add src/services/interview/session_manager.py src/types/api.py src/routes/interview.py tests/test_start_from_config.py
git commit -m "feat(config): add POST /interview/start-from-config using frozen plan"
```

---

## Task 11: Smooth outro — `WRAP_UP` candidate Q&A

**Files:**
- Create: `backend/src/services/interview/outro.py`
- Modify: `backend/src/routes/interview.py` (route into WRAP_UP after last question; handle WRAP_UP answers)
- Test: `backend/tests/test_outro_flow.py` (create)

**Design notes for the implementer:**
- After the last scored question is answered, the run goes to `WRAP_UP` instead of `EVALUATING`. Only the **config-based** flow uses this (config has `has_outro=True` via the stored plan); the legacy flow is unchanged.
- In `WRAP_UP`, each candidate message is answered by the LLM **constrained to JD/config context only**; unknown → "the recruiter can clarify". Routing is deterministic: a counter caps outro questions at `MAX_OUTRO_QUESTIONS` (default 3), after which the next message advances to `EVALUATING`. An explicit empty/"finish" sentinel from the client also advances. WRAP_UP turns are unscored.
- A new session counter `outro_questions_used` tracks the cap. Add it to `SessionState` as additive `outro_questions_used: int = 0`.

- [ ] **Step 1: Add the counter field**

In `backend/src/types/interview.py` `SessionState`, add:

```python
    outro_questions_used: int = 0
```

- [ ] **Step 2: Write the failing test for the outro answer service**

```python
# backend/tests/test_outro_flow.py
"""WRAP_UP outro candidate Q&A.

WHY: The outro must answer ONLY from JD/config context, fall back to a recruiter-
clarify line when the LLM can't answer, cap the number of candidate questions
deterministically, and never score these turns.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.services.interview.outro import answer_candidate_question, RECRUITER_FALLBACK


def _mock_response(text: str):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


def test_answer_uses_jd_context():
    client = MagicMock()
    client.messages.create.return_value = _mock_response("The role focuses on backend APIs.")
    with patch("src.services.interview.outro.get_anthropic_client", return_value=client):
        ans = answer_candidate_question(
            question="What does the role focus on?",
            job_role="backend engineer",
            jd_summary={"responsibilities": ["build APIs"], "skills": ["python"]},
        )
    assert "backend" in ans.lower()


def test_answer_falls_back_on_llm_error():
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("down")
    with patch("src.services.interview.outro.get_anthropic_client", return_value=client):
        ans = answer_candidate_question(
            question="What's the salary?", job_role="backend engineer", jd_summary={},
        )
    assert ans == RECRUITER_FALLBACK
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_outro_flow.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement the outro service**

```python
# backend/src/services/interview/outro.py
"""Constrained candidate Q&A for the WRAP_UP phase.

The LLM may answer ONLY from the provided JD/config context. Anything it cannot
answer from that context → a fixed recruiter-clarify line. This is drafting from
provided context (allowed), never routing.
"""
import json
import logging

from src.lib.anthropic_client import get_anthropic_client, get_model_for_task

logger = logging.getLogger(__name__)

MAX_OUTRO_QUESTIONS = 3
RECRUITER_FALLBACK = "That's a great question — the recruiter can clarify that for you."

_SYSTEM = (
    "You are wrapping up a job interview and answering the candidate's questions about "
    "the role. Answer ONLY using the job context provided below. If the answer is not "
    "contained in that context, reply EXACTLY with: " + RECRUITER_FALLBACK + " "
    "Keep answers to 1-3 sentences, warm and professional."
)


def answer_candidate_question(question: str, job_role: str, jd_summary: dict) -> str:
    context = json.dumps({"job_role": job_role, "jd_summary": jd_summary or {}})
    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=get_model_for_task("interview"),
            max_tokens=300,
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Job context: {context}\n\nCandidate question: {question}"}],
        )
        text = response.content[0].text.strip()
        return text or RECRUITER_FALLBACK
    except Exception as exc:
        logger.error("Outro answer failed: %s", exc)
        return RECRUITER_FALLBACK
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_outro_flow.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Wire WRAP_UP into `submit_answer`**

In `backend/src/routes/interview.py`, import the outro module:

```python
from src.services.interview.outro import answer_candidate_question, MAX_OUTRO_QUESTIONS, RECRUITER_FALLBACK
```

First, **update the state guard** (`interview.py:75-79`) so WRAP_UP submissions are not rejected with a 409. Add `InterviewState.WRAP_UP` to the allowed set:

```python
    if session.state not in (
        InterviewState.QUESTIONING,
        InterviewState.STARTED,
        InterviewState.WARMUP,
        InterviewState.WRAP_UP,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot submit answer in state: {session.state.value}",
        )
```

Then add a WRAP_UP branch **before** the existing `turn_manager.process_answer` call (right after the WARMUP block). Handle the candidate's outro question and the cap:

```python
    if session.state == InterviewState.WRAP_UP:
        session_manager.record_turn(session, speaker="candidate", text=body.answer)

        if session.outro_questions_used >= MAX_OUTRO_QUESTIONS:
            session.state = InterviewState.EVALUATING
            session_manager.update_session(session)
        else:
            session.outro_questions_used += 1
            reply = answer_candidate_question(
                question=body.answer,
                job_role=session.job_role,
                jd_summary=session.jd_summary or {},
            )
            session_manager.update_session(session)
            session_manager.record_turn(session, speaker="bot", text=reply)
            return SubmitAnswerResponse(
                session_id=body.session_id,
                state=InterviewState.WRAP_UP,
                next_question=reply,
                question_number=None,
                total_questions=len(session.questions),
                topic="wrap_up",
                is_complete=False,
            )
        # fall through to evaluation when the cap is hit
        if session.state == InterviewState.EVALUATING:
            return await _finalize_and_report(session, body.session_id)
```

- [ ] **Step 7: Route the last question into WRAP_UP (config flow only)**

`turn_manager.process_answer` returns `state == EVALUATING` after the last question. In `submit_answer`, change the post-`process_answer` handling so that **config-based** sessions enter WRAP_UP first. Replace the `if result.state == InterviewState.EVALUATING:` block's beginning with a branch:

```python
    result = await turn_manager.process_answer(session, body.answer)

    if result.state == InterviewState.EVALUATING:
        session = session_manager.get_session(body.session_id)
        # Config-based interviews get a closing Q&A before evaluation.
        if session.interview_config_id:
            session.state = InterviewState.WRAP_UP
            session_manager.update_session(session)
            closing = (
                f"That's the last of my questions, {session.candidate_name}. "
                "Before we wrap up — do you have any questions for me about the role?"
            )
            session_manager.record_turn(session, speaker="bot", text=closing)
            return SubmitAnswerResponse(
                session_id=body.session_id,
                state=InterviewState.WRAP_UP,
                score=result.score,
                score_reasoning=result.score_reasoning,
                next_question=closing,
                total_questions=len(session.questions),
                topic="wrap_up",
                is_complete=False,
            )
        return await _finalize_and_report(session, body.session_id)
```

- [ ] **Step 8: Extract `_finalize_and_report`**

The current `submit_answer` builds the evaluation, metrics, report, persists it, and returns the COMPLETE response **inline** under `if result.state == InterviewState.EVALUATING:` — this is exactly `interview.py:118-188` (the block from `evaluation = await llm_service.generate_final_evaluation(session)` down to the `return SubmitAnswerResponse(... is_complete=True, feedback=evaluation.summary)`).

Refactor — do NOT rewrite the body, **move it verbatim**:

1. Create a module-level `async def _finalize_and_report(session, session_id: str) -> SubmitAnswerResponse:` above `submit_answer`.
2. **Cut** the existing block at `interview.py:119-188` (everything after the `if result.state == InterviewState.EVALUATING:` line, i.e. starting at `evaluation = await llm_service.generate_final_evaluation(session)`) and **paste it unchanged** as the body of `_finalize_and_report`, with two mechanical substitutions only: the first line stays `evaluation = await llm_service.generate_final_evaluation(session)`, and replace `body.session_id` with the `session_id` parameter throughout the moved body. The final `return SubmitAnswerResponse(...)` already returns the COMPLETE response — keep it verbatim.
3. The `score_update` propagation lives in `turn_manager.process_answer` (writes `running_scores`) and in `generate_final_evaluation` (passes `question_results` into the report) — neither is touched, so propagation is preserved. The regression test in Task 13 locks this.

After the move, the only call sites that finalize are: the WRAP_UP-cap path (Step 6) and the legacy/non-config path (Step 7), both via `return await _finalize_and_report(session, body.session_id)`.

- [ ] **Step 9: Write the integration test for the WRAP_UP routing + cap**

```python
# append to backend/tests/test_outro_flow.py
from typing import Any
from unittest.mock import AsyncMock

from src.types.api import SubmitAnswerRequest
from src.types.config import InterviewConfig, InterviewPlan, JDSummary
from src.types.interview import (
    ExperienceLevel, InterviewState, Question, QuestionType, SessionState,
)

_S: dict[str, Any] = {}
def _sj(k, v, ttl=0): _S[k] = v
def _gj(k): return _S.get(k)


@pytest.fixture(autouse=True)
def _reset():
    _S.clear()
    yield
    _S.clear()


def _seed_wrapup_session() -> str:
    q = Question(id="a", topic="t", difficulty="medium",
                 question_type=QuestionType.CONCEPTUAL, experience_level="mid",
                 question_text="Q", rubric={"criteria": []})
    s = SessionState(
        session_id="sid", state=InterviewState.WRAP_UP, candidate_name="Alice",
        job_role="backend engineer", experience_level=ExperienceLevel.MID,
        questions=[q], current_question_idx=0, interview_config_id="cfg-1",
        jd_summary={"skills": ["python"]}, outro_questions_used=0,
    )
    _S["session:sid"] = s.model_dump()
    return "sid"


@pytest.mark.asyncio
async def test_wrapup_answer_is_unscored_and_stays_in_wrapup():
    from src.routes.interview import submit_answer
    sid = _seed_wrapup_session()
    with (
        patch("src.lib.redis_client.set_json", side_effect=_sj),
        patch("src.lib.redis_client.get_json", side_effect=_gj),
        patch("src.services.interview.outro.answer_candidate_question", return_value="Sure — it's backend focused."),
    ):
        resp = await submit_answer(SubmitAnswerRequest(session_id=sid, answer="What's the team size?"))
    assert resp.state == InterviewState.WRAP_UP
    assert resp.score is None


@pytest.mark.asyncio
async def test_wrapup_cap_advances_to_evaluation():
    from src.routes.interview import submit_answer
    sid = _seed_wrapup_session()
    s = SessionState(**_S["session:sid"])
    s.outro_questions_used = MAX_OUTRO_QUESTIONS
    _S["session:sid"] = s.model_dump()

    with (
        patch("src.lib.redis_client.set_json", side_effect=_sj),
        patch("src.lib.redis_client.get_json", side_effect=_gj),
        patch("src.routes.interview._finalize_and_report",
              new=AsyncMock(return_value=SubmitAnswerResponse(
                  session_id=sid, state=InterviewState.COMPLETE, is_complete=True, feedback="done"))),
    ):
        resp = await submit_answer(SubmitAnswerRequest(session_id=sid, answer="one more?"))
    assert resp.state == InterviewState.COMPLETE
    assert resp.is_complete is True
```

- [ ] **Step 10: Run the outro tests**

Run: `pytest tests/test_outro_flow.py -v`
Expected: PASS (4 passed).

- [ ] **Step 11: Run the full suite (the submit_answer refactor must not regress legacy flow)**

Run: `pytest -q`
Expected: all pass — especially `test_warmup_flow.py` and any score/report tests.

- [ ] **Step 12: Commit**

```bash
git add src/services/interview/outro.py src/types/interview.py src/routes/interview.py tests/test_outro_flow.py
git commit -m "feat(config): add smooth WRAP_UP outro with constrained candidate Q&A"
```

---

## Task 12: Frontend — admin create-config UI + candidate config start

**Files:**
- Modify: `frontend/src/services/api.ts` (add `createConfig`, `listConfigs`, `startFromConfig` + types)
- Create: `frontend/src/app/admin/configs/new/page.tsx`
- Create: `frontend/src/app/admin/configs/page.tsx`
- Modify: candidate start to accept `configId` (see Step 4)

**Note:** Match the existing `api.ts` `request<T>()` helper and `x-admin-key` header usage already present for admin calls. Read `frontend/src/services/api.ts` and an existing admin page (`frontend/src/app/admin/history/page.tsx`) first to copy conventions (auth header source, error handling, styling).

- [ ] **Step 1: Add API client functions**

In `frontend/src/services/api.ts`, add types and functions mirroring the existing `startInterview` helper and the admin-key pattern used by the history/analysis pages:

```typescript
export interface CreateConfigRequest {
  title: string;
  role: string;
  experience_level: "junior" | "mid" | "senior" | "staff";
  job_description: string;
  total_questions: number;
  core_question_ratio: number;
}

export interface ConfigResponse {
  id: string;
  title: string;
  role: string;
  experience_level: string;
  total_questions: number;
  core_question_ratio: number;
  created_at?: string | null;
}

export async function createConfig(
  body: CreateConfigRequest,
  adminKey: string
): Promise<ConfigResponse> {
  return request<ConfigResponse>("/api/v1/admin/configs", {
    method: "POST",
    headers: { "x-admin-key": adminKey },
    body: JSON.stringify(body),
  });
}

export async function listConfigs(
  adminKey: string
): Promise<{ configs: ConfigResponse[]; total: number }> {
  return request<{ configs: ConfigResponse[]; total: number }>(
    "/api/v1/admin/configs",
    { headers: { "x-admin-key": adminKey } }
  );
}

export interface StartFromConfigRequest {
  interview_config_id: string;
  candidate_name: string;
  resume_details?: Record<string, unknown> | null;
}

export async function startFromConfig(
  body: StartFromConfigRequest
): Promise<StartInterviewResponse> {
  return request<StartInterviewResponse>("/api/v1/interview/start-from-config", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
```

(If `request<T>()` does not merge a `headers` option, extend it minimally to spread caller headers over the defaults — check its current signature first and follow its existing shape.)

- [ ] **Step 2: Build the create-config form page**

Create `frontend/src/app/admin/configs/new/page.tsx`: a client component with fields `title`, `role`, `experience_level` (select), `job_description` (required textarea), `total_questions` (number, min 4), `core_question_ratio` (number, default 0.8, step 0.05). On submit, read the admin key the same way the existing admin pages do, call `createConfig`, show the returned config `id` and a link to start a candidate interview (`/interview/start?configId=<id>`). Surface validation/HTTP errors inline. Follow the styling and layout of `frontend/src/app/admin/history/page.tsx`.

- [ ] **Step 3: Build the configs list page**

Create `frontend/src/app/admin/configs/page.tsx`: fetch `listConfigs(adminKey)` and render a table (title, role, level, total_questions, created_at) with a "Create new" link to `/admin/configs/new` and a per-row "Start interview" link to `/interview/start?configId=<id>`.

- [ ] **Step 4: Wire candidate start to `configId`**

In the candidate start page (`frontend/src/app/interview/start/page.tsx`), read `configId` from the URL query (`useSearchParams`). When present, after collecting the candidate name (and using the `UserDetails` the existing resume-upload flow already produces), call `startFromConfig({ interview_config_id: configId, candidate_name, resume_details })` instead of the legacy `startInterview`. When absent, keep the existing legacy behavior unchanged. Persist the `StartInterviewResponse` into `sessionStorage` under the existing key `interview_session_{sessionId}` exactly as today, then navigate to `/interview/[sessionId]`.

- [ ] **Step 5: Manual verification**

Run backend + frontend (`npm run dev`). As admin: create a config, confirm it appears in the list. As candidate: open `/interview/start?configId=<id>`, start, and confirm the warmup is personalized (if resume details present) and the interview ends with the "any questions for me?" outro instead of stopping abruptly.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/services/api.ts frontend/src/app/admin/configs frontend/src/app/interview/start/page.tsx
git commit -m "feat(config): admin create/list config UI + candidate config-based start"
```

---

## Task 13: Regression guards (XML fallback + score propagation)

**Files:**
- Test: `backend/tests/test_config_regressions.py` (create)

These lock the spec's two "must-not-break" invariants in the context of the new flow.

- [ ] **Step 1: Write the guard tests**

```python
# backend/tests/test_config_regressions.py
"""Regression guards for the JD-config feature.

WHY: The new flow must not weaken two existing invariants:
(1) malformed XML from the interviewer LLM still falls back to acknowledge + raw text;
(2) a score_update parsed from the LLM still propagates to running_scores -> final report.
"""
from src.services.llm.response_parser import parse_xml_response


def test_malformed_xml_falls_back_to_acknowledge():
    parsed = parse_xml_response("total garbage, no tags here")
    assert parsed.action == "acknowledge"
    assert parsed.spoken_text == "total garbage, no tags here"
    assert parsed.next_state == "questioning"


def test_score_update_is_parsed_and_propagates():
    raw = """
    <interviewer_response>
      <action>acknowledge</action>
      <spoken_text>Thanks.</spoken_text>
      <internal_notes>solid</internal_notes>
      <confidence>0.9</confidence>
      <score_update>
        <topic>python</topic>
        <score>8</score>
        <reasoning>clear</reasoning>
      </score_update>
      <next_state>questioning</next_state>
      <flags></flags>
    </interviewer_response>
    """
    parsed = parse_xml_response(raw)
    assert parsed.score == 8.0
    assert parsed.score_topic == "python"
    # turn_manager writes running_scores[topic] = score (see turn_manager.process_answer),
    # and llm_service.generate_final_evaluation passes question_results into the report.
```

- [ ] **Step 2: Run the guards**

Run: `pytest tests/test_config_regressions.py -v`
Expected: PASS (2 passed).

- [ ] **Step 3: Full suite green**

Run: `pytest -q`
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_config_regressions.py
git commit -m "test(config): lock XML fallback + score propagation invariants"
```

---

## Final verification

- [ ] Run the whole backend suite from `backend/`: `pytest -q` — all green.
- [ ] Confirm the voice pipeline files were not modified: `git diff --name-only main... | grep -E 'voice_|websocket'` returns nothing.
- [ ] Confirm Postgres was not introduced: no new `psycopg`/`asyncpg`/PG writes; only `interviews.db` (SQLite) is touched.
- [ ] Spot-check determinism: create a config, reload it, and confirm identical plan question text/order.

---

## Spec coverage map

| Spec requirement | Task |
|---|---|
| `interview_configs` SQLite table, fail-loud create | 3 |
| Config types / `InterviewPlan` / `JDSummary` | 2 |
| 80/20 split inside total, JD floored | 2, 6 |
| JD analysis (LLM, fail-loud) | 5 |
| Frozen deterministic plan (core freeze + JD + behavioral + project, fixed order) | 4, 6 |
| `POST /admin/configs` + validation (JD required, total≥4, ratio range, insufficient questions) | 7 |
| `GET /admin/configs` | 8 |
| SessionState additive fields | 9, 11 |
| Resume warmup personalization (whitelist, no PII, graceful fallback) | 9 |
| `POST /interview/start-from-config` using frozen plan + 404 | 10 |
| `WRAP_UP` forward-only state | 1 |
| Smooth outro, constrained Q&A, deterministic cap, unscored | 11 |
| Legacy `/interview/start` untouched | 1, 11 (branch on `interview_config_id`) |
| Frontend admin UI + candidate config start | 12 |
| Tests: JD required, deterministic plan, 80/20, invalid config, XML fallback, score propagation, invalid transitions, PII, outro | 1–11, 13 |
| Voice/Postgres/`response_parser` XML untouched | Final verification |
