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
