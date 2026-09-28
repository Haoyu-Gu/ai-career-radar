from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from ..db import connect, rows
from .config_loader import load_yaml
from .factors import _present


PROFILE_LABEL = "顾昊瑜 · 研究能力校准"
VALUE_SCORE_METHOD = "50% 岗位数量 + 20% 研究热度 + 30% 低个人困难度"


def _log_max_scores(values: list[int | float]) -> list[float]:
    maximum = max((max(0.0, float(value)) for value in values), default=0.0)
    if maximum <= 0:
        return [0.0 for _ in values]
    denominator = math.log1p(maximum)
    return [round(100 * math.log1p(max(0.0, float(value))) / denominator, 1) for value in values]


def _growth_scores(values: list[float | None]) -> list[float]:
    materialized = [max(-1.0, min(2.0, float(value or 0))) for value in values]
    low, high = min(materialized, default=0), max(materialized, default=0)
    if high == low:
        return [50.0 for _ in materialized]
    return [round(100 * (value - low) / (high - low), 1) for value in materialized]


def personal_difficulty_by_direction() -> dict[str, dict[str, Any]]:
    """Estimate entry difficulty for the user's transferable profile, not topic familiarity."""
    with connect() as conn:
        jobs = rows(
            conn,
            """
            SELECT d.direction_slug, j.title, j.responsibilities, j.requirements
            FROM jobs j JOIN job_directions d ON d.job_id=j.id
            WHERE j.source_kind='peer_benchmark' AND j.status='reference' AND d.relevance='core'
            """,
        )

    factor_config = load_yaml("factors.yaml")["factors"]
    wanted = {
        "phd", "top_publication", "deployment", "open_source", "competition",
        "python_cpp", "pytorch", "english", "ai_infra",
    }
    terms = {item["slug"]: item["terms"] for item in factor_config if item["slug"] in wanted}
    grouped: dict[str, list[str]] = defaultdict(list)
    for job in jobs:
        grouped[job["direction_slug"]].append(
            "\n".join((job["title"] or "", job["responsibilities"] or "", job["requirements"] or "")).casefold()
        )

    # Academic signals carry a smaller penalty because the resume already demonstrates
    # independent top-venue research. Systems and production evidence remain the main gap.
    weights = {
        "phd": 10.0,
        "top_publication": 4.0,
        "deployment": 24.0,
        "open_source": 5.0,
        "competition": 3.0,
        "python_cpp": 4.0,
        "pytorch": 4.0,
        "english": 2.0,
        "ai_infra": 28.0,
    }
    result: dict[str, dict[str, Any]] = {}
    for slug, texts in grouped.items():
        total = len(texts)
        shares = {
            factor: sum(any(_present(term, text) for term in terms.get(factor, [])) for text in texts) / total
            for factor in weights
        }
        research_barrier = 10 * shares["phd"] + 4 * shares["top_publication"]
        engineering_barrier = sum(
            weights[factor] * shares[factor]
            for factor in ("deployment", "open_source", "competition", "python_cpp", "pytorch", "english")
        )
        systems_barrier = weights["ai_infra"] * shares["ai_infra"]
        difficulty = min(90.0, 8.0 + research_barrier + engineering_barrier + systems_barrier)
        result[slug] = {
            "score": round(difficulty, 1),
            "sample_size": total,
            "research_barrier": round(research_barrier, 1),
            "engineering_barrier": round(engineering_barrier, 1),
            "systems_barrier": round(systems_barrier, 1),
            "profile": PROFILE_LABEL,
            "note": "只按可迁移研究能力校准；不因简历已有研究方向而加分。",
        }
    return result


def attach_value_scores(
    direction_rows: list[dict[str, Any]],
    difficulty_by_slug: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach transparent 0-100 component scores and the composite DVI."""
    talent_scores = _log_max_scores([item["talent_program_jobs"] for item in direction_rows])
    open_scores = _log_max_scores([item["open_jobs"] for item in direction_rows])
    paper_scores = _log_max_scores([item["papers"] for item in direction_rows])
    author_scores = _log_max_scores([item["active_authors"] for item in direction_rows])
    growth_scores = _growth_scores([item.get("paper_growth") for item in direction_rows])

    for index, item in enumerate(direction_rows):
        job_score = 0.7 * talent_scores[index] + 0.3 * open_scores[index]
        research_heat_raw = (
            0.5 * paper_scores[index] + 0.3 * author_scores[index] + 0.2 * growth_scores[index]
            if item["papers"] or item["active_authors"] else 0.0
        )
        scope = item.get("research_scope") or {}
        coverage = float(scope.get("coverage_ratio") or 0)
        research_confidence = 1.0 if scope.get("is_complete") else min(0.6, math.sqrt(max(0.0, coverage)))
        # Incomplete retrieval remains a weak signal, never a fake census.
        research_heat_score = research_heat_raw * (0.25 + 0.75 * research_confidence)
        difficulty = difficulty_by_slug.get(item["slug"], {
            "score": 50.0,
            "sample_size": 0,
            "research_barrier": 0.0,
            "engineering_barrier": 0.0,
            "systems_barrier": 0.0,
            "profile": PROFILE_LABEL,
            "note": "没有足够 JD 样本，暂用中性困难度。",
        })
        value_score = 0.5 * job_score + 0.2 * research_heat_score + 0.3 * (100 - difficulty["score"])
        item.update({
            "value_score": round(value_score, 1),
            "job_score": round(job_score, 1),
            "research_heat_score": round(research_heat_score, 1),
            "research_heat_raw_score": round(research_heat_raw, 1),
            "research_confidence": round(research_confidence, 3),
            "personal_difficulty": difficulty,
            "value_score_method": VALUE_SCORE_METHOD,
        })
    return direction_rows
