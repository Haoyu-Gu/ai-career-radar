from __future__ import annotations

import itertools
import re
from collections import Counter
from typing import Any

from ..db import connect, rows
from .config_loader import load_yaml


def _present(term: str, text: str) -> bool:
    normalized = term.casefold()
    if re.fullmatch(r"[a-z0-9][a-z0-9 .+/_-]*", normalized):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text))
    return normalized in text


def mine_jd_factors(source_kind: str = "peer_benchmark") -> dict[str, Any]:
    status = "reference" if source_kind == "peer_benchmark" else "open"
    with connect() as conn:
        jobs = rows(
            conn,
            """SELECT id, company, title, responsibilities, requirements, talent_program
              FROM jobs WHERE source_kind=? AND status=?""",
            (source_kind, status),
        )
    config = load_yaml("factors.yaml")
    matched_by_job: dict[int, list[str]] = {}
    output: list[dict[str, Any]] = []
    for factor in config["factors"]:
        matched: list[dict[str, Any]] = []
        title_hits = requirement_hits = 0
        evidence_terms: Counter[str] = Counter()
        for job in jobs:
            title = (job["title"] or "").casefold()
            responsibilities = (job["responsibilities"] or "").casefold()
            requirements = (job["requirements"] or "").casefold()
            hits = [term for term in factor["terms"] if _present(term, "\n".join((title, responsibilities, requirements)))]
            if not hits:
                continue
            matched.append(job)
            matched_by_job.setdefault(job["id"], []).append(factor["slug"])
            title_hits += int(any(_present(term, title) for term in factor["terms"]))
            requirement_hits += int(any(_present(term, requirements) for term in factor["terms"]))
            evidence_terms.update(hits)
        output.append({
            "slug": factor["slug"],
            "name_zh": factor["name_zh"],
            "category": factor["category"],
            "jobs": len(matched),
            "share": round(len(matched) / len(jobs), 4) if jobs else 0,
            "companies": len({item["company"] for item in matched}),
            "programs": len({(item["company"], item["talent_program"]) for item in matched}),
            "title_hits": title_hits,
            "requirement_hits": requirement_hits,
            "top_terms": [{"term": term, "jobs": count} for term, count in evidence_terms.most_common(5)],
            "examples": [
                {"id": item["id"], "company": item["company"], "program": item["talent_program"], "title": item["title"]}
                for item in matched[:4]
            ],
        })
    output.sort(key=lambda item: (item["category"], -item["jobs"], item["name_zh"]))

    pair_counts: Counter[tuple[str, str]] = Counter()
    for factors in matched_by_job.values():
        pair_counts.update(itertools.combinations(sorted(set(factors)), 2))
    factor_names = {item["slug"]: item["name_zh"] for item in output}
    cooccurrences = [
        {"left": left, "left_name": factor_names[left], "right": right,
         "right_name": factor_names[right], "jobs": count}
        for (left, right), count in pair_counts.most_common(12)
    ]
    return {
        "source_kind": source_kind,
        "jobs_analyzed": len(jobs),
        "factors": output,
        "cooccurrences": cooccurrences,
        "method": "按 JD 标题、职责和要求做可复核的词组命中；同一岗位对同一因子只计一次。",
        "limitation": "这是需求文本的出现频率，不代表因果关系，也不等于最终录用人数。",
    }

