from src.services.interview.plan_floor import VOICE_FLOOR, assess_plan_capacity


def test_floor_is_five():
    assert VOICE_FLOOR == 5


def test_capacity_full_when_planner_meets_request():
    usable, shortfall = assess_plan_capacity(found=6, requested=6)
    assert (usable, shortfall) == (6, False)


def test_capacity_trims_overproduction_to_request():
    usable, shortfall = assess_plan_capacity(found=8, requested=6)
    assert (usable, shortfall) == (6, False)


def test_capacity_flags_shortfall_above_floor():
    usable, shortfall = assess_plan_capacity(found=6, requested=8)
    assert (usable, shortfall) == (6, True)


def test_capacity_below_floor_raises():
    import pytest
    from src.services.interview.plan_floor import TooThinError
    with pytest.raises(TooThinError):
        assess_plan_capacity(found=4, requested=8)
