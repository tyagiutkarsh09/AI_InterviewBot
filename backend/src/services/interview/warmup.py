import random
from typing import Callable

_TEMPLATES: list[Callable[[str, str], str]] = [
    lambda name, _role: f"How are you, {name}? How did your day go?",
    lambda name, _role: f"Before we dive in, {name} — what was your most recent role, and what brought you here today?",
    lambda name, role: f"To kick things off, {name} — where did you study, and how did you get into {role}?",
    lambda name, _role: f"Good to meet you, {name}. Anything exciting going on before we get started?",
]

_FOLLOWUP_TEMPLATES: list[Callable[[str, str], str]] = [
    lambda name, role: f"That's great to hear, {name}. What are you most looking forward to in your next {role} role?",
    lambda name, _role: f"Love it. And {name}, what kind of work gets you most excited these days?",
    lambda name, _role: f"Nice! {name}, outside of work — anything you've been enjoying lately, or just keeping busy?",
    lambda name, role: f"Sounds good, {name}. What first got you interested in {role} work?",
]


def generate_warmup_question(candidate_name: str, job_role: str) -> str:
    template = random.choice(_TEMPLATES)
    return template(candidate_name, job_role)


def generate_warmup_followup(candidate_name: str, job_role: str) -> str:
    template = random.choice(_FOLLOWUP_TEMPLATES)
    return template(candidate_name, job_role)


def generate_transition_message(candidate_name: str) -> str:
    return f"Thanks for sharing that, {candidate_name}! Now let's get into the technical questions."
