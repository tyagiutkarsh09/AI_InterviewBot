

# JD-Hyper-Personalized Questions — Backend Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the voice interview's bank-driven question selection with a single LLM "planner" that reads the uploaded JD (primary) + resume (optional) and produces a personalized, JD-grounded question set (~80% JD / 20% resume), each question carrying its own difficulty, expected-answer key points, and a soft time budget — scored, followed-up, and taught against those key points.

**Architecture:** A new sync `plan_interview()` call (offloaded via `asyncio.to_thread`, mirroring `analyze_jd`) returns a structured `InterviewPlanDraft`. A new assembler turns the draft into the existing frozen `InterviewPlan` of `Question` objects (reusing `order_easy_first`, plus a fixed behavioral and a JD-grounded project deep-dive). A two-step admin flow — `POST /voice/plan/preview` (generate + cache a draft, supports regenerate + a blocking "too thin" confirm) then `POST /voice/session/start-from-draft` (create the session) — exposes the preview/regenerate/warning UX the frontend (Phase 2) will consume. The static question bank is removed from the voice path; the text/admin-config flow is untouched.

**Tech Stack:** FastAPI, Pydantic, Anthropic SDK (Haiku), Redis (with in-memory fallback), pytest.

**Execution tags:** each task is tagged `[inline]` (small, do in-session) or `[subagent]` (isolated + meaty — dispatch a fresh subagent with ONLY that task and the listed files). Keep subagents cheap: hand them the task block + the named files, not this whole plan.

---

## Scope (this plan)

IN: planner module + prompt, draft/plan types, `time_budget_sec` field, assembler + special-question builders, evaluation/persona/teaching prompt changes, difficulty-aware follow-up cap, draft store, the two new endpoints, removal of the bank from the voice path.

OUT (Phase 2 / deferred): all frontend work (`voice/start/page.tsx`, `voice-api.ts`, preview UI, regenerate button, slider); inline question editing; any change to the text/admin-config flow; expanding `questions.json`; choosing a stronger planner model (flagged in the spec as an eval to run — this plan keeps Haiku but isolates the model behind `get_model_for_task("planner")` so it's a one-line swap).

**Pre-req check (do once, [inline]):** confirm the gold-standard JDs exist for the manual eval in Task 8:
Run: `ls "/c/Users/Acer/Downloads/" | grep -i jd` — expect the four `*JD*.md` files. They are the acceptance fixtures, not committed.

---

## Task 1: Add `time_budget_sec` to the `Question` model  `[inline]`

**Files:**
- Modify: `backend/src/types/interview.py:31-40`
- Test: `backend/tests/test_question_model.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_question_model.py
from src.types.interview import Question, QuestionType


def test_question_carries_optional_time_budget():
    # time_budget_sec is the soft per-question pacing hint the planner emits;
    # it must be optional so bank/legacy questions (which have none) still load.
    q = Question(
        id="jd_0", topic="GD&T", difficulty="medium",
        question_type=QuestionType.SCENARIO, experience_level="all",
        question_text="Walk me through a tolerance stack-up you analyzed.",
        time_budget_sec=150,
    )
    assert q.time_budget_sec == 150

    legacy = Question(
        id="q1", topic="x", difficulty="easy", question_type=QuestionType.CONCEPTUAL,
        experience_level="all", question_text="hi",
    )
    assert legacy.time_budget_sec is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_question_model.py -v`
Expected: FAIL — `TypeError`/`ValidationError`: unexpected keyword `time_budget_sec`.

- [ ] **Step 3: Add the field**

In `backend/src/types/interview.py`, add one line to `Question` (after `tags`):

```python
class Question(BaseModel):
    id: str
    topic: str
    difficulty: str
    question_type: QuestionType
    experience_level: str
    question_text: str
    follow_up_texts: list[str] = Field(default_factory=list)
    rubric: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    time_budget_sec: Optional[int] = None  # soft pacing hint; planner-set, None for bank Qs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_question_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/types/interview.py backend/tests/test_question_model.py
git commit -m "feat(types): add optional time_budget_sec to Question"
```

---

## Task 2: The combined planner — `plan_interview()` + prompt  `[subagent]`

The centerpiece. One LLM call, JD + resume → structured plan. Sync (offloaded by the caller), mirroring `analyze_jd`. Fails loud.

**Files:**
- Create: `backend/src/types/planning.py`
- Create: `backend/src/services/llm/interview_planner.py`
- Create: `backend/src/prompts/interview_planner_prompt.txt`
- Modify: `backend/src/lib/anthropic_client.py:30-38` (register a `"planner"` task)
- Test: `backend/tests/test_interview_planner.py`

- [ ] **Step 1: Register the planner model task**

In `backend/src/lib/anthropic_client.py`, add one entry to the `models` dict in `get_model_for_task` (isolates the model so the spec's "evaluate a stronger model" stays a one-line change):

```python
    models = {
        "interview": "claude-haiku-4-5-20251001",
        "evaluation": "claude-haiku-4-5-20251001",
        "follow_up": "claude-haiku-4-5-20251001",
        "compression": "claude-haiku-4-5-20251001",
        "jd_analysis": "claude-haiku-4-5-20251001",
        "planner": "claude-haiku-4-5-20251001",
    }
```

- [ ] **Step 2: Define the plan types**

```python
# backend/src/types/planning.py
from pydantic import BaseModel, Field


class PlannedQuestion(BaseModel):
    competency: str                         # which JD/resume skill this probes
    source: str                             # "jd" | "resume"
    question_text: str
    difficulty: str                         # "easy" | "medium" | "hard"
    rubric_keypoints: list[str] = Field(default_factory=list)  # 3-5 expected points
    time_budget_sec: int = 120              # soft pacing hint


class InterviewPlanDraft(BaseModel):
    role_title: str                         # derived from JD, for intro + report
    skills: list[str] = Field(default_factory=list)
    questions: list[PlannedQuestion] = Field(default_factory=list)  # technical, jd+resume
    project_question_text: str = ""         # JD/resume-grounded project deep-dive
```

- [ ] **Step 3: Write the planner prompt**

```
# backend/src/prompts/interview_planner_prompt.txt
You are a senior technical recruiter with 20 years of experience. You are designing a
voice interview that is driven by THIS job description (the primary source) and, when
provided, personalized with the candidate's resume.

Return ONLY a single valid JSON object, no prose:

{
  "role_title": "the role title taken from the JD (e.g. 'Sr. Mechanical Design Engineer')",
  "skills": ["up to 8 concrete skills/tools/competencies named or strongly implied in the JD/resume"],
  "questions": [
    {
      "competency": "the JD skill this probes (e.g. 'GD&T', 'React', 'Azure deployment')",
      "source": "jd" | "resume",
      "question_text": "the exact question the interviewer will ask, in natural speech, one or two sentences",
      "difficulty": "easy" | "medium" | "hard",
      "rubric_keypoints": ["3 to 5 specific points a strong answer must contain"],
      "time_budget_sec": 60-210
    }
  ],
  "project_question_text": "a 'walk me through a relevant project' question grounded in the JD's domain and, if a resume is given, in a real project from it"
}

HARD RULES:
- Produce EXACTLY {num_questions} entries in "questions" if the JD supports it. If the JD
  is too thin to ground that many DISTINCT questions, produce as many as you genuinely can
  and STOP — never pad, repeat, or invent topics absent from the JD/resume.
- Aim for ~80% of the questions with source "jd" and ~20% with source "resume". If no
  resume is provided, ALL questions are source "jd".
- Weight questions toward the most prominent competencies in the JD (a core skill gets
  more questions than a peripheral one). Decide the distribution yourself from the JD.
- Difficulty: calibrate to the candidate's experience level ({experience_level}) AND the
  JD's seniority signals. Order the list so the first two questions are the EASIEST.
- Vary question style across the set: some direct/recall ("Do you have experience with X?"),
  some applied, some scenario/design, some troubleshooting. Deployment-type skills should
  ask the candidate to walk through what they actually did.
- rubric_keypoints are the expected-answer checklist for scoring — make them specific and
  technical, not generic ("clear communication").
- time_budget_sec scales with depth: ~60-90 for recall, ~120-150 for applied, ~150-210 for
  deep scenario/design questions.
- NEVER ask about family, age, gender, nationality, religion, marital status, health, or any
  personal/protected-class topic. Work and role competencies ONLY.
- Stay strictly within the JD (and resume). Do not introduce technologies the JD never mentions.

Role (admin-provided, may be generic — prefer the JD's own title): {job_role}
Experience level: {experience_level}
Target number of technical questions: {num_questions}

JOB DESCRIPTION (primary):
{jd_text}

CANDIDATE RESUME (optional personalization; empty if none provided):
{resume_text}
```

- [ ] **Step 4: Write the failing tests**

```python
# backend/tests/test_interview_planner.py
import json
from unittest.mock import patch, MagicMock

import pytest

from src.services.llm.interview_planner import plan_interview, PlannerError
from src.types.interview import ExperienceLevel


def _fake_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps(payload))]
    return resp


_GOOD = {
    "role_title": "Sr. Mechanical Design Engineer",
    "skills": ["GD&T", "Creo", "NPD"],
    "questions": [
        {"competency": "Creo", "source": "jd", "question_text": "Do you use Creo daily?",
         "difficulty": "easy", "rubric_keypoints": ["named modules", "real parts"], "time_budget_sec": 60},
        {"competency": "GD&T", "source": "jd", "question_text": "Walk me through a tolerance stack-up.",
         "difficulty": "hard", "rubric_keypoints": ["datum order", "modifiers", "inspection"], "time_budget_sec": 180},
    ],
    "project_question_text": "Walk me through a medical-device part you designed end to end.",
}


def test_plan_interview_parses_structured_plan():
    client = MagicMock()
    client.messages.create.return_value = _fake_response(_GOOD)
    with patch("src.services.llm.interview_planner.get_anthropic_client", return_value=client):
        draft = plan_interview("a JD", None, "Mechanical Engineer", ExperienceLevel.SENIOR, num_questions=2)
    assert draft.role_title == "Sr. Mechanical Design Engineer"
    assert [q.source for q in draft.questions] == ["jd", "jd"]
    assert draft.questions[1].rubric_keypoints == ["datum order", "modifiers", "inspection"]
    assert draft.project_question_text


def test_plan_interview_raises_on_no_questions():
    # A JD that yields zero grounded questions is a hard, loud failure (Rule 9) — the caller
    # turns this into the "too thin" error rather than starting a hollow interview.
    bad = {"role_title": "X", "skills": [], "questions": [], "project_question_text": "p"}
    client = MagicMock()
    client.messages.create.return_value = _fake_response(bad)
    with patch("src.services.llm.interview_planner.get_anthropic_client", return_value=client):
        with pytest.raises(PlannerError):
            plan_interview("thin jd", None, "role", ExperienceLevel.MID, num_questions=5)


def test_plan_interview_raises_on_malformed_json():
    client = MagicMock()
    client.messages.create.return_value = MagicMock(content=[MagicMock(text="not json")])
    with patch("src.services.llm.interview_planner.get_anthropic_client", return_value=client):
        with pytest.raises(PlannerError):
            plan_interview("jd", None, "role", ExperienceLevel.MID, num_questions=5)
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_interview_planner.py -v`
Expected: FAIL — `ModuleNotFoundError: src.services.llm.interview_planner`.

- [ ] **Step 6: Implement the planner**

```python
# backend/src/services/llm/interview_planner.py
"""Combined JD+resume interview planner via LLM. Fails loud.

ONE call: reads the JD (primary) and optional resume, returns a structured
InterviewPlanDraft (role title, skills, technical questions with per-question
difficulty + rubric key points + time budget, and a grounded project question).
Sync — callers in the async voice path must offload via asyncio.to_thread, like
analyze_jd. Mirrors jd_analysis.py: extraction/generation only, never routing.
"""
import json
import logging
import os

from src.lib.anthropic_client import get_anthropic_client, get_model_for_task
from src.types.interview import ExperienceLevel
from src.types.planning import InterviewPlanDraft, PlannedQuestion

logger = logging.getLogger(__name__)

_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "prompts", "interview_planner_prompt.txt"
)
_VALID_DIFFICULTY = {"easy", "medium", "hard"}


class PlannerError(RuntimeError):
    """Raised when the planner cannot produce a usable plan."""


def plan_interview(
    jd_text: str,
    resume_text: str | None,
    job_role: str,
    experience_level: ExperienceLevel,
    num_questions: int,
) -> InterviewPlanDraft:
    with open(_PROMPT_PATH, encoding="utf-8") as f:
        template = f.read()
    prompt = (
        template
        .replace("{num_questions}", str(num_questions))
        .replace("{experience_level}", experience_level.value)
        .replace("{job_role}", job_role)
        .replace("{jd_text}", jd_text)
        .replace("{resume_text}", resume_text or "(none provided)")
    )

    try:
        client = get_anthropic_client()
        response = client.messages.create(
            model=get_model_for_task("planner"),
            max_tokens=2500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
    except Exception as exc:
        logger.error("Interview planner LLM call failed: %s", exc)
        raise PlannerError(f"Interview planner LLM call failed: {exc}") from exc

    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        raise PlannerError("Planner returned no JSON object")
    try:
        data = json.loads(raw[start:end])
    except json.JSONDecodeError as exc:
        raise PlannerError(f"Planner returned invalid JSON: {exc}") from exc

    questions: list[PlannedQuestion] = []
    for q in data.get("questions", []):
        text = str(q.get("question_text", "")).strip()
        if not text:
            continue
        difficulty = str(q.get("difficulty", "medium")).lower()
        if difficulty not in _VALID_DIFFICULTY:
            difficulty = "medium"
        source = "resume" if str(q.get("source", "")).lower() == "resume" else "jd"
        try:
            budget = int(q.get("time_budget_sec", 120))
        except (TypeError, ValueError):
            budget = 120
        budget = max(45, min(budget, 240))
        questions.append(PlannedQuestion(
            competency=str(q.get("competency", "")).strip() or "role-specific",
            source=source,
            question_text=text,
            difficulty=difficulty,
            rubric_keypoints=[str(k).strip() for k in q.get("rubric_keypoints", []) if str(k).strip()][:5],
            time_budget_sec=budget,
        ))

    if not questions:
        raise PlannerError("Planner produced no usable questions")

    return InterviewPlanDraft(
        role_title=str(data.get("role_title", "")).strip() or job_role,
        skills=[str(s).strip() for s in data.get("skills", []) if str(s).strip()][:8],
        questions=questions,
        project_question_text=str(data.get("project_question_text", "")).strip(),
    )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_interview_planner.py -v`
Expected: PASS (3 tests).

- [ ] **Step 8: Commit**

```bash
git add backend/src/types/planning.py backend/src/services/llm/interview_planner.py \
  backend/src/prompts/interview_planner_prompt.txt backend/src/lib/anthropic_client.py \
  backend/tests/test_interview_planner.py
git commit -m "feat(llm): combined JD+resume interview planner with per-question rubrics"
```

---

## Task 3: Special-question builders carry generated metadata  `[inline]`

`build_jd_question`/`build_resume_question` currently hardcode `difficulty="medium"` + a generic rubric. Make them carry the planner's difficulty, key points, source, and time budget; let `build_project_question` take grounded text.

**Files:**
- Modify: `backend/src/services/interview/special_questions.py:41-77`
- Test: `backend/tests/test_special_questions.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_special_questions.py
from src.services.interview.special_questions import (
    build_planned_question, build_project_question, build_behavioral_question,
)
from src.types.planning import PlannedQuestion


def test_build_planned_question_preserves_planner_metadata():
    pq = PlannedQuestion(
        competency="GD&T", source="jd", question_text="Explain datum referencing.",
        difficulty="hard", rubric_keypoints=["datum order", "modifiers"], time_budget_sec=180,
    )
    q = build_planned_question(pq, index=2)
    assert q.id == "jd_2"
    assert q.topic == "GD&T"
    assert q.difficulty == "hard"
    assert q.rubric == {"key_points": ["datum order", "modifiers"]}
    assert q.time_budget_sec == 180
    assert q.tags == ["jd_generated"]


def test_build_project_question_uses_grounded_text_when_given():
    q = build_project_question("Walk me through the fixture you designed at Acme.")
    assert "fixture" in q.question_text
    # falls back to the generic template when text is empty
    assert build_project_question("").question_text


def test_behavioral_question_is_unchanged_fixed_template():
    assert "disagreed" in build_behavioral_question().question_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_special_questions.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_planned_question'`.

- [ ] **Step 3: Update the builders**

In `backend/src/services/interview/special_questions.py`: add the import and `build_planned_question`, replace `build_jd_question`/`build_resume_question` with it, and give `build_project_question` an optional grounded-text param. Leave `build_behavioral_question` as-is.

```python
from src.types.interview import Question, QuestionType
from src.types.planning import PlannedQuestion  # add at top


def build_planned_question(pq: PlannedQuestion, index: int) -> Question:
    """Wrap a planner question into the Question model the run/eval pipeline consumes.

    rubric_keypoints live under rubric["key_points"] so the existing eval prompt
    (which json.dumps(question.rubric)) feeds them to the scorer unchanged.
    """
    return Question(
        id=f"{pq.source}_{index}",
        topic=pq.competency or "role-specific",
        difficulty=pq.difficulty,
        question_type=QuestionType.SCENARIO,
        experience_level="all",
        question_text=pq.question_text,
        rubric={"key_points": pq.rubric_keypoints},
        tags=[f"{pq.source}_generated"],
        time_budget_sec=pq.time_budget_sec,
    )


def build_project_question(grounded_text: str = "") -> Question:
    return Question(
        id="project_0",
        topic="project deep-dive",
        difficulty="medium",
        question_type=QuestionType.SCENARIO,
        experience_level="all",
        question_text=grounded_text.strip() or _PROJECT_TEXT,
        rubric=_GENERIC_RUBRIC,
        tags=["project_deepdive"],
    )
```

Delete the old `build_jd_question` and `build_resume_question` functions (replaced by `build_planned_question`). Keep `_GENERIC_RUBRIC`, `_BEHAVIORAL_TEXT`, `_PROJECT_TEXT`, and `build_behavioral_question`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_special_questions.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/interview/special_questions.py backend/tests/test_special_questions.py
git commit -m "feat(interview): planned-question builder carries difficulty+keypoints+budget"
```

---

## Task 4: Assembler — draft → frozen `InterviewPlan`  `[subagent]`

Turn an `InterviewPlanDraft` into the ordered `InterviewPlan`: easy-first technical questions, then fixed behavioral, then JD-grounded project. Replaces `build_voice_plan` (bank-based). Also a floor/achievable helper.

**Files:**
- Modify: `backend/src/services/interview/plan_builder.py` (add `assemble_voice_plan`; keep `order_easy_first` + `build_plan`; the old `build_voice_plan` is removed in Task 7 once callers are gone)
- Create: `backend/src/services/interview/plan_floor.py`
- Test: `backend/tests/test_assemble_voice_plan.py`
- Test: `backend/tests/test_plan_floor.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_plan_floor.py
from src.services.interview.plan_floor import VOICE_FLOOR, assess_plan_capacity


def test_floor_is_five():
    assert VOICE_FLOOR == 5


def test_capacity_full_when_planner_meets_request():
    # 6 grounded questions, asked for 6 -> usable 6, no shortfall.
    usable, shortfall = assess_plan_capacity(found=6, requested=6)
    assert (usable, shortfall) == (6, False)


def test_capacity_trims_overproduction_to_request():
    usable, shortfall = assess_plan_capacity(found=8, requested=6)
    assert (usable, shortfall) == (6, False)


def test_capacity_flags_shortfall_above_floor():
    # asked 8, only 6 grounded -> usable 6 but shortfall True (admin must confirm).
    usable, shortfall = assess_plan_capacity(found=6, requested=8)
    assert (usable, shortfall) == (6, True)


def test_capacity_below_floor_raises():
    import pytest
    from src.services.interview.plan_floor import TooThinError
    with pytest.raises(TooThinError):
        assess_plan_capacity(found=4, requested=8)
```

```python
# backend/tests/test_assemble_voice_plan.py
from src.services.interview.plan_builder import assemble_voice_plan
from src.types.planning import InterviewPlanDraft, PlannedQuestion


def _q(comp, diff, src="jd"):
    return PlannedQuestion(competency=comp, source=src, question_text=f"Q about {comp}?",
                           difficulty=diff, rubric_keypoints=["a"], time_budget_sec=120)


def test_assemble_orders_easy_first_then_behavioral_then_project():
    draft = InterviewPlanDraft(
        role_title="Sr ME", skills=["GD&T"],
        questions=[_q("GD&T", "hard"), _q("Creo", "easy"), _q("NPD", "medium")],
        project_question_text="Walk me through your fixture project.",
    )
    plan = assemble_voice_plan(draft, usable_count=3)
    texts = [q.question_text for q in plan.questions]
    # easy-first ramp: the two easiest technical questions lead.
    assert plan.questions[0].difficulty == "easy"
    # last two are behavioral then the grounded project deep-dive.
    assert "disagreed" in texts[-2]
    assert "fixture" in texts[-1]


def test_assemble_respects_usable_count_trim():
    draft = InterviewPlanDraft(
        role_title="r", skills=[],
        questions=[_q("a", "easy"), _q("b", "medium"), _q("c", "hard")],
        project_question_text="proj",
    )
    plan = assemble_voice_plan(draft, usable_count=2)
    # 2 technical + behavioral + project = 4
    assert len(plan.questions) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_plan_floor.py tests/test_assemble_voice_plan.py -v`
Expected: FAIL — missing module `plan_floor` / missing `assemble_voice_plan`.

- [ ] **Step 3: Implement the floor helper**

```python
# backend/src/services/interview/plan_floor.py
"""Deterministic capacity math for a JD-driven voice plan.

The planner (LLM) decides the JD/resume mix and may under-produce on a thin JD.
This module owns the COUNTING: trim overproduction to the request, flag a
shortfall (admin must confirm a reduced interview), and hard-fail below the floor.
"""

VOICE_FLOOR = 5  # minimum viable technical questions for a real interview


class TooThinError(RuntimeError):
    """Raised when a JD yields fewer than VOICE_FLOOR grounded questions."""


def assess_plan_capacity(found: int, requested: int) -> tuple[int, bool]:
    """Return (usable_count, shortfall).

    usable_count = min(found, requested). shortfall = the JD could not meet the
    request but is still viable (usable >= VOICE_FLOOR) -> caller asks the admin to
    confirm. Below the floor is a hard TooThinError (Rule 9: never run a hollow set).
    """
    usable = min(found, requested)
    if usable < VOICE_FLOOR:
        raise TooThinError(
            f"Job description yields only {found} grounded questions; "
            f"need at least {VOICE_FLOOR}."
        )
    return usable, found < requested
```

- [ ] **Step 4: Implement the assembler**

Append to `backend/src/services/interview/plan_builder.py` (add imports at top alongside existing ones; `order_easy_first` and `build_plan` stay):

```python
from src.services.interview.special_questions import (
    build_behavioral_question,
    build_planned_question,
    build_project_question,
)
from src.types.planning import InterviewPlanDraft


def assemble_voice_plan(draft: InterviewPlanDraft, usable_count: int) -> InterviewPlan:
    """Draft -> frozen plan: easy-first technical (jd+resume) -> behavioral -> project.

    usable_count caps the technical questions (Task 4 floor math decides it). Behavioral
    is fixed; the project deep-dive is grounded in the draft's project_question_text.
    """
    technical = [
        build_planned_question(pq, index=i)
        for i, pq in enumerate(draft.questions[:usable_count])
    ]
    technical = order_easy_first(technical)
    questions = technical + [
        build_behavioral_question(),
        build_project_question(draft.project_question_text),
    ]
    return InterviewPlan(questions=questions)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_plan_floor.py tests/test_assemble_voice_plan.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/src/services/interview/plan_builder.py backend/src/services/interview/plan_floor.py \
  backend/tests/test_plan_floor.py backend/tests/test_assemble_voice_plan.py
git commit -m "feat(interview): assemble JD-driven voice plan with floor/shortfall math"
```

---

## Task 5: Evaluation, persona & teaching prompt changes  `[subagent]`

Resolve the "never give hints" conflict, add the persona, make scoring use `rubric.key_points`, calibrate follow-ups, and protect score integrity. These are prompt edits plus two string-assertable builder behaviors.

**Files:**
- Modify: `backend/src/prompts/system_prompt.txt`
- Modify: `backend/src/services/llm/prompt_builder.py:19-63` (`build_answer_evaluation_prompt`)
- Test: `backend/tests/test_eval_prompt_contract.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_eval_prompt_contract.py
from src.services.llm.prompt_builder import build_answer_evaluation_prompt, build_system_prompt
from src.types.interview import Question, QuestionType, SessionState, ExperienceLevel


def _session(level):
    return SessionState(session_id="s", candidate_name="A", job_role="ME",
                        experience_level=level, questions=[], required_skills=["GD&T"])


def test_system_prompt_has_persona_and_teaching_not_hint_ban():
    p = build_system_prompt()
    assert "20 years" in p                       # persona present
    assert "never give away answers or provide hints" not in p  # old absolute ban removed
    assert "no worries" in p.lower() or "concede" in p.lower()  # teaching-on-concession policy


def test_eval_prompt_surfaces_keypoints_and_experience():
    q = Question(id="jd_0", topic="GD&T", difficulty="hard", question_type=QuestionType.SCENARIO,
                 experience_level="all", question_text="Explain stack-ups.",
                 rubric={"key_points": ["datum order", "modifiers"]})
    prompt = build_answer_evaluation_prompt(q, "some answer", _session(ExperienceLevel.SENIOR))
    assert "datum order" in prompt          # key points reach the scorer
    assert "senior" in prompt               # experience calibrates the follow-up
    assert "key_points" in prompt or "key points" in prompt.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_eval_prompt_contract.py -v`
Expected: FAIL — persona/teaching strings absent; key-points instruction absent.

- [ ] **Step 3: Edit `system_prompt.txt`**

Replace the line `- You never give away answers or provide hints` (under YOUR ROLE) and prepend a persona. The top of the file becomes:

```
You are a warm, seasoned senior recruiter and technical interviewer with 20 years of experience across technical and non-technical roles. You put candidates at ease, never condescend, and are never rude or dismissive — regardless of how a candidate performs or how senior they are. You calibrate the depth of your questions and follow-ups to the candidate's experience level, and you stay strictly within the role's job description.

## YOUR ROLE
- You are fair, professional, and encouraging
- You evaluate answers objectively using the provided rubric key points
- While a candidate is still attempting an answer, you probe WITHOUT giving the answer away — a hint would inflate the score
- Once a candidate concedes ("I don't know") or the question is exhausted, drop the assessment posture: say "no worries", give a brief, accurate explanation (2-3 sentences) framed around the question's rubric key points, then move on
- Teaching is selective, not automatic — explain more for junior candidates who are stuck; for senior candidates usually acknowledge warmly and move on rather than lecture. It is a job interview, not a tutorial
- You keep responses concise and conversational
```

(Leave the RESPONSE FORMAT / CONFIDENCE / SCORING / INTERVIEW RULES sections unchanged.)

- [ ] **Step 4: Edit `build_answer_evaluation_prompt`**

In `backend/src/services/llm/prompt_builder.py`, the `<current_question>` block and `<turn_instruction>` change to surface key points, the candidate's level, and the scoring-integrity + teaching rules. Replace lines 40-60 region with:

```python
    key_points = question.rubric.get("key_points") if isinstance(question.rubric, dict) else None
    key_points_text = json.dumps(key_points) if key_points else json.dumps(question.rubric)

    return f"""
<candidate_info>
  Name: {session.candidate_name}
  Role: {session.job_role}
  Level: {session.experience_level.value}
  Required skills: {', '.join(session.required_skills) or 'general'}
</candidate_info>

<interview_progress>
  Current question: {session.current_question_idx + 1} of {len(session.questions)}
  Follow-ups used: {session.follow_up_count}
</interview_progress>

<current_question>
  Question: {question.question_text}
  Topic: {question.topic}
  Difficulty: {question.difficulty}
  Expected answer key_points: {key_points_text}
</current_question>

<conversation_history>
{transcript_text}
</conversation_history>

<running_scores>
{json.dumps(session.running_scores, indent=2)}
</running_scores>

<turn_instruction>
Score the candidate's answer for topic "{question.topic}" by checking it against the
expected key_points above — reward the points they actually covered, not confident phrasing.
The score MUST reflect only what the CANDIDATE said: if they did not know, score low even if
you go on to explain the answer. Never let your own explanation raise the score.

Then decide the next action:
- If the answer is comprehensive, set action "acknowledge" (or "evaluating" if last question).
- If a key point is missing and probing is worthwhile, set action "follow_up" and put ONE
  follow-up in spoken_text that targets the MISSING key point, pitched to a {session.experience_level.value}
  candidate. Stay within this question's topic — never introduce a topic outside the job description.
- If the candidate concedes or has been probed enough, acknowledge warmly ("no worries"),
  optionally give a brief 2-3 sentence explanation built from the key_points (more for juniors,
  usually skip for seniors), and prepare to move on.
</turn_instruction>

Candidate's answer: {answer}
"""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_eval_prompt_contract.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/src/prompts/system_prompt.txt backend/src/services/llm/prompt_builder.py \
  backend/tests/test_eval_prompt_contract.py
git commit -m "feat(eval): persona, teaching-on-concession, key-point scoring, calibrated follow-ups"
```

---

## Task 6: Difficulty-aware follow-up cap  `[inline]`

`voice_llm_orchestrator.py` hardcodes `MAX_FOLLOW_UPS = 2`. Spec: 1 per question, 2 for `hard`.

**Files:**
- Modify: `backend/src/services/interview/voice_llm_orchestrator.py:266-269`
- Test: `backend/tests/test_follow_up_cap.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_follow_up_cap.py
from src.services.interview.voice_llm_orchestrator import max_follow_ups_for
from src.types.interview import Question, QuestionType


def _q(diff):
    return Question(id="x", topic="t", difficulty=diff, question_type=QuestionType.SCENARIO,
                    experience_level="all", question_text="q")


def test_hard_question_allows_two_follow_ups():
    assert max_follow_ups_for(_q("hard")) == 2


def test_non_hard_questions_allow_one():
    assert max_follow_ups_for(_q("medium")) == 1
    assert max_follow_ups_for(_q("easy")) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_follow_up_cap.py -v`
Expected: FAIL — `ImportError: cannot import name 'max_follow_ups_for'`.

- [ ] **Step 3: Extract + use the helper**

In `backend/src/services/interview/voice_llm_orchestrator.py`, add the helper near the top (module level):

```python
def max_follow_ups_for(question: Question) -> int:
    """1 follow-up per question, 2 for hard ones (code-enforced cap; LLM decides whether
    to use the budget). Keeps a 5-question interview from becoming an interrogation."""
    return 2 if question.difficulty.lower() == "hard" else 1
```

Then replace the hardcoded cap at lines 266-269:

```python
    action = parsed.action
    follow_up_count = int(voice_data.get("follow_up_count", 0))
    MAX_FOLLOW_UPS = max_follow_ups_for(current_q)

    if action in ("acknowledge", "transition") or follow_up_count >= MAX_FOLLOW_UPS:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_follow_up_cap.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/interview/voice_llm_orchestrator.py backend/tests/test_follow_up_cap.py
git commit -m "feat(voice): difficulty-aware follow-up cap (1, or 2 for hard)"
```

---

## Task 7: Draft store + endpoints; remove the bank from voice  `[subagent]`

The integration task. Two endpoints: `preview` (generate + cache a draft; supports regenerate by re-calling; returns a `needs_confirmation` flag on shortfall) and `start-from-draft` (build the session). Removes the bank-based path and the old `build_voice_plan` usage.

**Files:**
- Create: `backend/src/services/interview/plan_draft_store.py`
- Modify: `backend/src/routes/voice_api.py` (replace `start_voice_session_from_jd`; drop bank imports)
- Modify: `backend/src/services/interview/plan_builder.py` (remove the now-dead `build_voice_plan` + `compute_voice_split` import) and `backend/src/services/interview/plan_math.py` (remove `compute_voice_split`)
- Test: `backend/tests/test_plan_draft_store.py`
- Test: `backend/tests/test_voice_plan_endpoints.py`

- [ ] **Step 1: Write the failing tests for the draft store**

```python
# backend/tests/test_plan_draft_store.py
from src.services.interview.plan_draft_store import save_plan_draft, get_plan_draft


def test_draft_round_trips():
    payload = {"role_title": "ME", "questions": [{"competency": "GD&T"}], "usable_count": 5}
    draft_id = save_plan_draft(payload)
    assert isinstance(draft_id, str) and draft_id
    got = get_plan_draft(draft_id)
    assert got["role_title"] == "ME"
    assert got["usable_count"] == 5


def test_missing_draft_returns_none():
    assert get_plan_draft("nope-not-a-real-id") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_plan_draft_store.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the draft store** (mirrors `voice_session.py` Redis-with-memory-fallback)

```python
# backend/src/services/interview/plan_draft_store.py
"""Short-lived storage for a generated interview plan draft.

The admin previews a generated plan (and can regenerate) BEFORE a session exists,
so the draft lives in Redis (1h TTL) under plan_draft:{id}. Falls back to an
in-memory dict when Redis is down, mirroring services/audio/voice_session.py.
"""
import json
import logging
import os
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

DRAFT_TTL = 3600  # 1 hour
_redis_client = None
_use_memory_fallback = False
_MEMORY: dict[str, str] = {}


def _client():
    global _redis_client, _use_memory_fallback
    if _use_memory_fallback:
        return None
    if _redis_client is not None:
        return _redis_client
    import redis as _redis  # type: ignore[import-untyped]
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        c = _redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        c.ping()
        _redis_client = c
        return _redis_client
    except Exception as exc:
        logger.warning("Redis unavailable for plan drafts (%s), using in-memory store", exc)
        _use_memory_fallback = True
        return None


def _key(draft_id: str) -> str:
    return f"plan_draft:{draft_id}"


def save_plan_draft(payload: dict[str, Any]) -> str:
    draft_id = str(uuid.uuid4())
    blob = json.dumps(payload)
    client = _client()
    if client:
        client.set(_key(draft_id), blob, ex=DRAFT_TTL)
    else:
        _MEMORY[draft_id] = blob
    return draft_id


def get_plan_draft(draft_id: str) -> Optional[dict[str, Any]]:
    client = _client()
    blob = client.get(_key(draft_id)) if client else _MEMORY.get(draft_id)
    return json.loads(blob) if blob else None
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_plan_draft_store.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Write the failing endpoint tests**

```python
# backend/tests/test_voice_plan_endpoints.py
import io
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.main import app
from src.types.planning import InterviewPlanDraft, PlannedQuestion

client = TestClient(app)
ADMIN = {"X-Admin-Key": "test-admin-key"}


def _pq(comp, diff, src="jd"):
    return PlannedQuestion(competency=comp, source=src, question_text=f"Q {comp}?",
                           difficulty=diff, rubric_keypoints=["a"], time_budget_sec=120)


def _draft(n):
    return InterviewPlanDraft(role_title="Sr ME", skills=["GD&T"],
                             questions=[_pq(f"c{i}", "medium") for i in range(n)],
                             project_question_text="walk me through a project")


def _files():
    return {"jd": ("jd.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")}


def test_preview_requires_admin():
    r = client.post("/api/v1/voice/plan/preview", files=_files(),
                    data={"job_role": "ME", "num_questions": "5"})
    assert r.status_code == 401  # guard fires before any extraction/LLM


def test_preview_returns_draft_and_questions():
    with patch("src.routes.voice_api.extract_jd_text", return_value="jd text"), \
         patch("src.routes.voice_api.plan_interview", return_value=_draft(5)), \
         patch("src.routes.voice_api.require_admin", return_value=None):
        r = client.post("/api/v1/voice/plan/preview", headers=ADMIN, files=_files(),
                        data={"job_role": "ME", "num_questions": "5"})
    assert r.status_code == 200
    body = r.json()
    assert body["draft_id"]
    assert len(body["questions"]) == 5
    assert body["needs_confirmation"] is False
    assert body["role_title"] == "Sr ME"


def test_preview_flags_shortfall_for_confirmation():
    # asked 8, planner grounded only 6 -> 200 with needs_confirmation True + usable 6.
    with patch("src.routes.voice_api.extract_jd_text", return_value="jd text"), \
         patch("src.routes.voice_api.plan_interview", return_value=_draft(6)), \
         patch("src.routes.voice_api.require_admin", return_value=None):
        r = client.post("/api/v1/voice/plan/preview", headers=ADMIN, files=_files(),
                        data={"job_role": "ME", "num_questions": "8"})
    assert r.status_code == 200
    body = r.json()
    assert body["needs_confirmation"] is True
    assert body["usable_count"] == 6


def test_preview_too_thin_is_422():
    with patch("src.routes.voice_api.extract_jd_text", return_value="jd text"), \
         patch("src.routes.voice_api.plan_interview", return_value=_draft(3)), \
         patch("src.routes.voice_api.require_admin", return_value=None):
        r = client.post("/api/v1/voice/plan/preview", headers=ADMIN, files=_files(),
                        data={"job_role": "ME", "num_questions": "5"})
    assert r.status_code == 422


def test_start_from_draft_creates_session():
    with patch("src.routes.voice_api.extract_jd_text", return_value="jd text"), \
         patch("src.routes.voice_api.plan_interview", return_value=_draft(5)), \
         patch("src.routes.voice_api.require_admin", return_value=None):
        pv = client.post("/api/v1/voice/plan/preview", headers=ADMIN, files=_files(),
                         data={"job_role": "ME", "num_questions": "5"})
    draft_id = pv.json()["draft_id"]
    with patch("src.routes.voice_api.require_admin", return_value=None):
        r = client.post("/api/v1/voice/session/start-from-draft", headers=ADMIN,
                        json={"draft_id": draft_id, "candidate_name": "Alex"})
    assert r.status_code == 201
    assert r.json()["session_id"]
    assert r.json()["ws_url"]
```

> Note for the implementer: if the admin-key fixture name differs, read `backend/src/routes/admin.py:require_admin` and `backend/tests/test_admin_create_config.py` for the exact header/value convention and match it. The `require_admin` patch in the happy-path tests bypasses the real key check so the test focuses on flow.

- [ ] **Step 6: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_voice_plan_endpoints.py -v`
Expected: FAIL — endpoints `/voice/plan/preview` and `/voice/session/start-from-draft` do not exist (404).

- [ ] **Step 7: Implement the endpoints**

In `backend/src/routes/voice_api.py`:

(a) Replace the bank/JD imports (lines 27-33) with:

```python
from src.services.interview.plan_builder import assemble_voice_plan
from src.services.interview.plan_floor import assess_plan_capacity, TooThinError, VOICE_FLOOR
from src.services.interview.plan_draft_store import save_plan_draft, get_plan_draft
from src.services.interview.warmup import generate_introduction, build_ease_in
from src.services.llm.interview_planner import plan_interview, PlannerError
from src.types.config import JDSummary
from src.types.interview import ExperienceLevel, Question
```

(Remove imports of `build_voice_plan`, `InsufficientQuestionsError`, `analyze_jd`, `analyze_resume`, `get_question_set`, `eligible_question_count` — they are no longer used by voice.)

(b) Add the constants and request/response models near the existing ones:

```python
MIN_QUESTIONS = 5
MAX_QUESTIONS = 8


class PlanPreviewResponse(BaseModel):
    draft_id: str
    role_title: str
    questions: list[dict]
    requested: int
    usable_count: int
    needs_confirmation: bool


class StartFromDraftRequest(BaseModel):
    draft_id: str
    candidate_name: str = "Candidate"
```

(c) Delete the old `start_voice_session_from_jd` function (lines ~136-281) and add the two endpoints:

```python
@router.post(
    "/plan/preview",
    response_model=PlanPreviewResponse,
    dependencies=[Depends(require_admin)],
)
async def preview_plan(
    jd: UploadFile = File(...),
    resume: Optional[UploadFile] = File(None),
    job_role: str = Form(...),
    experience_level: ExperienceLevel = Form(ExperienceLevel.MID),
    num_questions: int = Form(MIN_QUESTIONS),
) -> PlanPreviewResponse:
    if not (MIN_QUESTIONS <= num_questions <= MAX_QUESTIONS):
        raise HTTPException(422, f"num_questions must be between {MIN_QUESTIONS} and {MAX_QUESTIONS}.")

    jd_bytes = await jd.read()
    try:
        jd_text = await asyncio.to_thread(extract_jd_text, jd.filename or "", jd_bytes)
    except JDExtractError:
        raise HTTPException(422, "Could not read the job description file.")

    resume_text: Optional[str] = None
    if resume is not None:
        resume_bytes = await resume.read()
        try:
            resume_text = await asyncio.to_thread(extract_jd_text, resume.filename or "", resume_bytes)
        except JDExtractError:
            raise HTTPException(422, "Could not read the resume file.")

    try:
        draft = await asyncio.to_thread(
            plan_interview, jd_text, resume_text, job_role, experience_level, num_questions
        )
    except PlannerError as exc:
        logger.error("Planner failed: %s", exc)
        raise HTTPException(502, "Could not analyze the job description. Try again.")

    try:
        usable_count, shortfall = assess_plan_capacity(len(draft.questions), num_questions)
    except TooThinError as exc:
        raise HTTPException(422, str(exc))

    # Cache the full draft + the resolved usable_count + role/skills so start-from-draft
    # rebuilds the exact previewed plan without re-calling the LLM.
    draft_id = save_plan_draft({
        "draft": draft.model_dump(),
        "usable_count": usable_count,
        "job_role": job_role,
        "experience_level": experience_level.value,
    })
    return PlanPreviewResponse(
        draft_id=draft_id,
        role_title=draft.role_title,
        questions=[pq.model_dump() for pq in draft.questions[:usable_count]],
        requested=num_questions,
        usable_count=usable_count,
        needs_confirmation=shortfall,
    )


@router.post(
    "/session/start-from-draft",
    response_model=VoiceSessionStartResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def start_from_draft(body: StartFromDraftRequest, request: Request) -> VoiceSessionStartResponse:
    cached = get_plan_draft(body.draft_id)
    if cached is None:
        raise HTTPException(404, "Plan draft not found or expired. Please regenerate.")

    draft = InterviewPlanDraft(**cached["draft"])
    plan = assemble_voice_plan(draft, usable_count=int(cached["usable_count"]))

    session_id = str(uuid.uuid4())
    job_role = draft.role_title or cached["job_role"]
    intro_text = generate_introduction(body.candidate_name, job_role, len(plan.questions))
    ease_in_text = build_ease_in(body.candidate_name)
    jd_summary = JDSummary(skills=draft.skills)
    create_voice_session(
        session_id=session_id,
        candidate_name=body.candidate_name,
        job_role=job_role,
        experience_level=cached["experience_level"],
        required_skills=draft.skills,
        questions_json=_json.dumps([q.model_dump() for q in plan.questions]),
        intro_text=intro_text,
        ease_in_text=ease_in_text,
        jd_summary_json=_json.dumps(jd_summary.model_dump()),
    )
    logger.info("Voice session from draft session=%s role=%s questions=%d",
                session_id, job_role, len(plan.questions))

    token = _issue_token(session_id)
    ws_base = os.getenv("VOICE_WS_BASE")
    if not ws_base:
        scheme = "wss" if request.url.scheme == "https" else "ws"
        ws_base = f"{scheme}://{request.url.netloc}"
    return VoiceSessionStartResponse(
        session_id=session_id, token=token, state="INITIALIZING",
        ws_url=f"{ws_base}/ws/interview/voice/{session_id}?token={token}",
    )
```

Add `from src.types.planning import InterviewPlanDraft` to the imports.

- [ ] **Step 8: Remove the now-dead bank/voice-split code**

Delete `build_voice_plan` from `backend/src/services/interview/plan_builder.py` and `compute_voice_split` from `backend/src/services/interview/plan_math.py`, plus their tests `backend/tests/test_build_voice_plan.py` and the `compute_voice_split` cases in `backend/tests/test_plan_math.py`. (`build_plan`/`compute_split` stay — the text/admin flow still uses them.) Also delete the obsolete `backend/tests/test_voice_start_from_jd.py` (replaced by `test_voice_plan_endpoints.py`).

- [ ] **Step 9: Run the endpoint + neighbor tests**

Run: `cd backend && python -m pytest tests/test_voice_plan_endpoints.py tests/test_plan_math.py tests/test_plan_builder.py -v`
Expected: PASS (endpoints green; `build_plan`/`compute_split` still green).

- [ ] **Step 10: Commit**

```bash
git add backend/src/routes/voice_api.py backend/src/services/interview/plan_draft_store.py \
  backend/src/services/interview/plan_builder.py backend/src/services/interview/plan_math.py \
  backend/tests/test_plan_draft_store.py backend/tests/test_voice_plan_endpoints.py \
  backend/tests/test_build_voice_plan.py backend/tests/test_voice_start_from_jd.py backend/tests/test_plan_math.py
git commit -m "feat(voice): JD-driven preview/start-from-draft endpoints; remove question bank from voice"
```

---

## Task 8: Full regression + manual gold-standard eval  `[inline]`

**Files:** none (verification only).

- [ ] **Step 1: Run the backend suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: green except the known-red baseline (per memory: ~5 `opening_quality` failures are pre-existing; the suite finishes ~6s then may hang on orphaned aiosqlite threads — if it hangs after printing results, that is the known issue, not a new failure). Confirm NO new failures beyond that baseline. Any import errors from removed bank symbols → fix the stale import and re-run.

- [ ] **Step 2: Manual gold-standard generation eval (acceptance bar)**

With a real `ANTHROPIC_API_KEY` in `backend/.env`, run the planner against each of the four gold-standard JDs and eyeball the output. Quick harness:

```bash
cd backend && python -c "
from src.lib.jd_extract import extract_jd_text
from src.services.llm.interview_planner import plan_interview
from src.types.interview import ExperienceLevel
import glob, json
for path in glob.glob('/c/Users/Acer/Downloads/*JD*.md') + glob.glob('/c/Users/Acer/Downloads/*Engineer*.md'):
    text = open(path, encoding='utf-8').read()
    d = plan_interview(text, None, 'Mechanical Engineer', ExperienceLevel.SENIOR, 8)
    print('===', path.split('/')[-1], '-> role:', d.role_title, '| questions:', len(d.questions))
    for q in d.questions:
        print(f'  [{q.difficulty}] ({q.source}/{q.competency}) {q.question_text}')
"
```

Acceptance (spec §G): each gold-standard JD yields **6–8 grounded, on-domain questions**, difficulty varies, and key points are specific (not "clear communication"). If a JD comes back thin or generic, that is the signal to evaluate a stronger planner model (swap the one line in `get_model_for_task("planner")`) — record the finding; do not weaken the floor to hide it.

- [ ] **Step 3: Final commit (if any eval-driven prompt tweaks were made)**

```bash
git add -A && git commit -m "chore(planner): tune prompt against gold-standard JDs"
```

---

## Self-Review (done while writing — recorded for the implementer)

- **Spec coverage:** JD mandatory+primary → Task 7 (`jd: UploadFile = File(...)`). Combined planner → Task 2. 80/20 + auto-allocation → Task 2 prompt (LLM) + Task 4 (code trims/orders). Per-question rubric/difficulty/type/time → Tasks 1,2,3. Easy-first ramp → Task 4 (`order_easy_first`). Behavioral kept + project JD-grounded → Tasks 3,4. Key-point scoring + scoring integrity → Task 5. Teaching policy + persona (one prompt file, covers voice+text) → Task 5. Follow-up cap 1/2 → Task 6. Calibrated + grounded follow-ups → Task 5 prompt. Soft time budget → Tasks 1-3 (field carried; spoken-framing is consumed by the orchestrator, which already speaks `question_text`; explicit "spoken on deep Qs" wording is a Phase-2 orchestrator tweak — noted below). Thin-JD warn-and-shrink + floor 5 + hard-fail below floor → Task 4 (`assess_plan_capacity`) + Task 7 (`needs_confirmation`, 422). Preview + regenerate (re-call preview) + blocking confirm → Task 7. Bank removed from voice → Task 7. Role from JD → Task 7 (`draft.role_title`).
- **Carried to Phase 2 (frontend) / explicitly deferred:** making the soft time budget *spoken only on deep questions* (orchestrator wording — the field exists now); the slider 5–8, JD-mandatory form, preview render, regenerate button, and blocking confirm dialog (all frontend). These consume the Task-7 contract.
- **Type consistency:** `InterviewPlanDraft`/`PlannedQuestion` (Task 2) are consumed unchanged by Tasks 3,4,7. `build_planned_question(pq, index)`, `assemble_voice_plan(draft, usable_count)`, `assess_plan_capacity(found, requested)`, `max_follow_ups_for(question)`, `save_plan_draft/get_plan_draft`, `plan_interview(jd_text, resume_text, job_role, experience_level, num_questions)` — names used identically across tasks. `rubric={"key_points": [...]}` written in Task 3 is read in Task 5.
```
