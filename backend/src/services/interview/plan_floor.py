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
