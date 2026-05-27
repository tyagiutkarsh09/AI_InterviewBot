import json
import os
import random
from typing import Optional
from src.types.interview import Question, ExperienceLevel

_QUESTIONS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "data", "questions.json"
)

_cache: Optional[list[Question]] = None


def _load_all() -> list[Question]:
    global _cache
    if _cache is None:
        with open(_QUESTIONS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        _cache = [Question(**q) for q in raw]
    return _cache


def _level_rank(level: str) -> int:
    return {"junior": 0, "mid": 1, "senior": 2, "staff": 3, "all": -1}.get(level, 0)


def get_question_set(
    job_role: str,
    experience_level: ExperienceLevel,
    required_skills: list[str],
    count: int = 5,
) -> list[Question]:
    all_q = _load_all()
    candidate_rank = _level_rank(experience_level.value)

    skill_tags = {s.lower().replace(" ", "_") for s in required_skills}
    role_keywords = set(job_role.lower().split())

    def score_question(q: Question) -> float:
        q_rank = _level_rank(q.experience_level)
        if q_rank > candidate_rank and q.experience_level != "all":
            return -1.0

        s = 0.0
        q_tags = {t.lower() for t in q.tags}
        s += len(skill_tags & q_tags) * 2.0

        if any(k in q.topic.lower() for k in role_keywords):
            s += 1.5

        if q.experience_level == experience_level.value:
            s += 1.0
        elif q.experience_level == "all":
            s += 0.5

        return s

    scored = [(score_question(q), q) for q in all_q]
    eligible = [(sc, q) for sc, q in scored if sc >= 0]
    eligible.sort(key=lambda x: x[0], reverse=True)

    top_pool = eligible[: max(count * 3, 10)]
    if len(top_pool) <= count:
        return [q for _, q in top_pool]

    weights = [max(sc + 0.5, 0.1) for sc, _ in top_pool]
    chosen = random.choices(
        [q for _, q in top_pool], weights=weights, k=min(count, len(top_pool))
    )

    seen: set[str] = set()
    unique: list[Question] = []
    for q in chosen:
        if q.id not in seen:
            seen.add(q.id)
            unique.append(q)

    if len(unique) < count:
        remaining = [q for _, q in eligible if q.id not in seen]
        unique.extend(remaining[: count - len(unique)])

    return unique[:count]


def get_question_by_id(question_id: str) -> Optional[Question]:
    return next((q for q in _load_all() if q.id == question_id), None)
