"""Import and evaluate an external talent-program crawl without calling it live data."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from ..db import connect, json_dumps, rows, utcnow
from .classify import (
    infer_education,
    infer_experience,
    infer_recruitment_type,
    infer_work_nature,
    job_direction_matches,
)
from .config_loader import load_directions


BENCHMARK_NAMESPACE = "peer-talent-programs-2026-09"
BENCHMARK_SOURCE = "peer-benchmark-2026-09"

OFFICIAL_PROGRAM_URLS = {
    "腾讯": "https://join.qq.com/",
    "阿里巴巴": "https://talent.alibaba.com/",
    "字节跳动": "https://jobs.bytedance.com/campus/position",
    "蚂蚁集团": "https://talent.antgroup.com/",
    "美团": "https://zhaopin.meituan.com/web/beidouprogram",
    "小米": "https://hr.xiaomi.com/",
    "快手": "https://zhaopin.kuaishou.cn/",
    "京东": "https://campus.jd.com/",
    "小红书": "https://job.xiaohongshu.com/campus",
    "哔哩哔哩": "https://jobs.bilibili.com/",
    "百度": "https://talent.baidu.com/jobs/list",
    "拼多多": "https://careers.pinduoduo.com/",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("name", "i18n_name", "en_name", "title"):
            if value.get(key):
                return str(value[key]).strip()
        return json_dumps(value)
    if isinstance(value, list):
        return "; ".join(filter(None, (_text(item) for item in value)))
    return str(value).strip()


def _department(record: dict[str, Any]) -> str:
    for key in (
        "business_group", "business", "group", "domain_name", "job_category",
        "category", "family", "post_code_name", "research_title",
    ):
        value = _text(record.get(key))
        if value:
            return value
    return ""


def _content_hash(*values: Any) -> str:
    return hashlib.sha256("\n".join(str(value or "") for value in values).encode()).hexdigest()


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"验收基准不存在：{source}")
    records: list[dict[str, Any]] = []
    with source.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {line_number} 行不是合法 JSON：{exc.msg}") from exc
            missing = [key for key in ("company", "program", "id", "title") if record.get(key) in (None, "")]
            if missing:
                raise ValueError(f"第 {line_number} 行缺少字段：{', '.join(missing)}")
            records.append(record)
    return records


def _normalize(record: dict[str, Any]) -> dict[str, Any]:
    title = _text(record.get("title")) or "未命名岗位"
    description = _text(record.get("description"))
    requirement = _text(record.get("requirement"))
    full_text = "\n".join(filter(None, (title, description, requirement)))
    type_text = _text(record.get("type"))
    recruitment_type = infer_recruitment_type("\n".join((title, type_text, full_text)))
    url = _text(record.get("position_url")) or OFFICIAL_PROGRAM_URLS.get(record["company"], "")
    category = _text(record.get("job_category") or record.get("category") or record.get("domain_name"))
    business_group = _text(record.get("business_group") or record.get("business") or record.get("group"))
    return {
        "external_id": str(record["id"]),
        "company": _text(record["company"]),
        "program": _text(record["program"]),
        "title": title,
        "department": _department(record),
        "location": _text(record.get("location")),
        "description": description,
        "requirement": requirement,
        "url": url,
        "published_at": _text(record.get("publish_date") or record.get("push_time")) or None,
        "recruitment_type": recruitment_type,
        "work_nature": infer_work_nature(title, full_text),
        "education": infer_education(full_text),
        "experience": infer_experience(full_text),
        "job_category": category,
        "business_group": business_group,
        "raw": record,
        "full_text": full_text,
    }


def import_benchmark(path: str | Path) -> dict[str, Any]:
    """Store the peer crawl as a reference snapshot, never as a live/open job."""
    raw_records = load_jsonl(path)
    normalized = [_normalize(record) for record in raw_records]
    keys = [(item["company"], item["external_id"]) for item in normalized]
    if len(keys) != len(set(keys)):
        duplicates = len(keys) - len(set(keys))
        raise ValueError(f"验收基准存在 {duplicates} 个重复的 company + id")

    now = utcnow()
    direction_version = load_directions()["version"]
    imported_ids: list[int] = []
    with connect() as conn:
        for item in normalized:
            content_hash = _content_hash(
                item["title"], item["department"], item["location"],
                item["description"], item["requirement"], item["program"],
            )
            existing = conn.execute(
                "SELECT id FROM jobs WHERE source_namespace=? AND company=? AND external_id=?",
                (BENCHMARK_NAMESPACE, item["company"], item["external_id"]),
            ).fetchone()
            values = (
                item["title"], item["department"], item["location"], item["recruitment_type"],
                item["work_nature"], item["education"], item["experience"], item["description"],
                item["requirement"], item["url"], item["published_at"], now, content_hash,
                json_dumps(item["raw"]), item["program"], item["job_category"], item["business_group"],
            )
            if existing:
                job_id = existing[0]
                conn.execute(
                    """
                    UPDATE jobs SET title=?, department=?, location=?, recruitment_type=?, work_nature=?,
                      education=?, experience=?, responsibilities=?, requirements=?, url=?, published_at=?,
                      last_seen_at=?, content_hash=?, raw_json=?, talent_program=?, job_category=?,
                      business_group=?, status='reference', source_kind='peer_benchmark' WHERE id=?
                    """,
                    (*values, job_id),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO jobs(source_namespace, source_slug, company, company_group, market,
                      external_id, title, department, location, recruitment_type, work_nature, education,
                      experience, responsibilities, requirements, benefits_raw, salary_raw, url,
                      published_at, first_seen_at, last_seen_at, status, headcount, content_hash, raw_json,
                      source_kind, talent_program, job_category, business_group)
                    VALUES(?, ?, ?, ?, 'cn_internet', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', NULL, ?, ?, ?, ?,
                      'reference', NULL, ?, ?, 'peer_benchmark', ?, ?, ?)
                    """,
                    (
                        BENCHMARK_NAMESPACE, BENCHMARK_SOURCE, item["company"], item["company"],
                        item["external_id"], item["title"], item["department"], item["location"],
                        item["recruitment_type"], item["work_nature"], item["education"], item["experience"],
                        item["description"], item["requirement"], item["url"], item["published_at"], now,
                        now, content_hash, json_dumps(item["raw"]), item["program"], item["job_category"],
                        item["business_group"],
                    ),
                )
                job_id = int(cursor.lastrowid)
            imported_ids.append(job_id)
            conn.execute("DELETE FROM job_directions WHERE job_id=?", (job_id,))
            for match in job_direction_matches(item["title"], "\n".join((item["description"], item["requirement"]))):
                conn.execute(
                    """
                    INSERT INTO job_directions(job_id, direction_slug, relevance, confidence, rationale,
                      classifier, version) VALUES(?, ?, ?, ?, ?, 'rules', ?)
                    """,
                    (job_id, match["slug"], match["relevance"], match["confidence"], match["rationale"], direction_version),
                )
        if imported_ids:
            placeholders = ",".join("?" for _ in imported_ids)
            conn.execute(
                f"DELETE FROM jobs WHERE source_namespace=? AND id NOT IN ({placeholders})",
                (BENCHMARK_NAMESPACE, *imported_ids),
            )

    return benchmark_summary()


def _completeness(records: Iterable[dict[str, Any]]) -> dict[str, float]:
    materialized = list(records)
    total = len(materialized)
    fields = ("title", "location", "responsibilities", "requirements", "talent_program")
    return {
        field: round(sum(bool(item.get(field)) for item in materialized) / total, 4) if total else 0
        for field in fields
    }


def benchmark_summary() -> dict[str, Any]:
    with connect() as conn:
        reference = rows(
            conn,
            """SELECT company, external_id, title, location, responsibilities, requirements,
              talent_program FROM jobs WHERE source_namespace=? AND status='reference'""",
            (BENCHMARK_NAMESPACE,),
        )
        live = rows(
            conn,
            """SELECT company, external_id, title, location, responsibilities, requirements,
              talent_program FROM jobs WHERE source_kind='live' AND status='open'""",
        )
        latest_job_scans = rows(
            conn,
            """
            SELECT s.company, b.reported_total, b.fetched_count, b.relevant_count, b.status
            FROM scan_batches b JOIN sources s ON s.slug=b.source_slug
            WHERE b.kind='jobs' AND b.id=(
              SELECT b2.id FROM scan_batches b2
              WHERE b2.source_slug=b.source_slug AND b2.kind='jobs'
              ORDER BY b2.started_at DESC, b2.rowid DESC LIMIT 1
            )
            """,
        )

    ref_keys = {(item["company"], item["external_id"]) for item in reference}
    live_keys = {(item["company"], item["external_id"]) for item in live}
    exact = ref_keys & live_keys
    live_title_location = {
        (item["company"], item["title"].strip().casefold(), (item["location"] or "").strip().casefold())
        for item in live
    }
    fuzzy = {
        (item["company"], item["external_id"])
        for item in reference
        if (item["company"], item["title"].strip().casefold(), (item["location"] or "").strip().casefold())
        in live_title_location
    }
    matched_keys = exact | fuzzy
    targets = Counter(item["company"] for item in reference)
    exact_by_company = Counter(company for company, _ in exact)
    fuzzy_by_company = Counter(company for company, _ in fuzzy)
    matched_by_company = Counter(company for company, _ in matched_keys)
    scan_by_company = {item["company"]: item for item in latest_job_scans}
    companies = []
    for company, target in targets.most_common():
        exact_count = exact_by_company[company]
        fuzzy_count = fuzzy_by_company[company]
        matched_count = matched_by_company[company]
        live_count = sum(item["company"] == company for item in live)
        scan = scan_by_company.get(company) or {}
        companies.append({
            "company": company,
            "target": target,
            "live_jobs": live_count,
            "official_reported": int(scan.get("reported_total") or 0),
            "official_fetched": int(scan.get("fetched_count") or 0),
            "scan_status": scan.get("status"),
            "exact_matches": exact_count,
            "title_location_matches": fuzzy_count,
            "matched_jobs": matched_count,
            "recall": round(matched_count / target, 4) if target else 0,
        })

    programs = [
        {"company": company, "program": program, "target": count}
        for (company, program), count in Counter(
            (item["company"], item["talent_program"]) for item in reference
        ).most_common()
    ]
    matched = len(matched_keys)
    official_fetched = sum(
        int(item.get("fetched_count") or 0)
        for item in latest_job_scans if item["company"] in targets
    )
    return {
        "available": bool(reference),
        "target_jobs": len(reference),
        "target_companies": len(targets),
        "target_programs": len(programs),
        "live_jobs_at_target_companies": sum(item["company"] in targets for item in live),
        "exact_matches": len(exact),
        "title_location_matches": len(fuzzy),
        "matched_jobs": matched,
        "official_fetched_jobs": official_fetched,
        "official_quantity_ratio": round(official_fetched / len(reference), 4) if reference else None,
        "job_recall": round(matched / len(reference), 4) if reference else None,
        "company_coverage": round(sum(bool(matched_by_company[c]) for c in targets) / len(targets), 4) if targets else None,
        "reference_completeness": _completeness(reference),
        "live_completeness": _completeness([item for item in live if item["company"] in targets]),
        "companies": companies,
        "programs": programs,
        "note": "外部 JSONL 只作为验收快照；官网原始抓取量、AI 相关岗位和逐条匹配分开统计。",
    }
