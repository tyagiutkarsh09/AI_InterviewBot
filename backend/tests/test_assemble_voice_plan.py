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
    assert plan.questions[0].difficulty == "easy"
    assert "disagreed" in texts[-2]
    assert "fixture" in texts[-1]


def test_assemble_respects_usable_count_trim():
    draft = InterviewPlanDraft(
        role_title="r", skills=[],
        questions=[_q("a", "easy"), _q("b", "medium"), _q("c", "hard")],
        project_question_text="proj",
    )
    plan = assemble_voice_plan(draft, usable_count=2)
    assert len(plan.questions) == 4
