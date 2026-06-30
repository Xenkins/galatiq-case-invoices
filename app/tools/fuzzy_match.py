from __future__ import annotations

from difflib import SequenceMatcher
from typing import Iterable, List

from app.schemas.models import MatchCandidate


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def rank_candidates(value: str, candidates: Iterable[str], top_k: int = 3) -> List[MatchCandidate]:
    scored = [
        MatchCandidate(candidate=option, confidence=round(similarity(value, option), 3))
        for option in candidates
    ]
    scored.sort(key=lambda c: c.confidence, reverse=True)
    return scored[:top_k]
