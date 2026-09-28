import calendar
from collections import Counter
from datetime import UTC, date, datetime
from statistics import median
from typing import Any

from dateutil.relativedelta import relativedelta

from ..db import connect, rows, utcnow
from ..settings import settings
from .forecast import forecast_series
from .fx import current_usd_cny
from .benchmark import benchmark_summary
from .direction_value import attach_value_scores, personal_difficulty_by_direction


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    weight = index - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def snapshot_current_jobs() -> None:
    captured = date.today().isoformat()
    with connect() as conn:
        direction_rows = conn.execute("SELECT slug FROM directions").fetchall()
        for row in direction_rows:
            slug = row[0]
            count = conn.execute(
                """
                SELECT COUNT(DISTINCT j.id) FROM jobs j
                JOIN job_directions d ON d.job_id=j.id
                WHERE d.direction_slug=? AND j.status='open'
                """,
                (slug,),
            ).fetchone()[0]
            sources = conn.execute(
                """
                SELECT COUNT(DISTINCT source_slug) FROM jobs j
                JOIN job_directions d ON d.job_id=j.id
                WHERE d.direction_slug=? AND j.status='open'
                """,
                (slug,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT OR REPLACE INTO snapshots(captured_at, metric, scope_key, value, sample_size,
                  completeness, source_note, config_version)
                VALUES(?, 'open_jobs', ?, ?, ?, 'complete_connected_sources',
                  '仅包含当次成功完整扫描的已接入来源；新增来源需单独解释',
                  (SELECT config_version FROM directions WHERE slug=?))
                """,
                (captured, slug, count, sources, slug),
            )


def coverage() -> dict[str, Any]:
    with connect() as conn:
        totals = conn.execute(
            """
            SELECT COUNT(*) candidates,
              SUM(status != 'pending' AND status != 'unchecked') checked,
              SUM(status = 'connected') connected,
              SUM(status IN ('failed','blocked')) failed
            FROM sources
            """
        ).fetchone()
        scans = conn.execute(
            """
            SELECT SUM(status='complete') complete_scans, SUM(status='partial') partial_scans,
              SUM(status='failed') failed_scans, COALESCE(SUM(reported_total),0) reported_total,
              COALESCE(SUM(fetched_count),0) fetched_total,
              COALESCE(SUM(CASE WHEN kind='jobs' THEN reported_total ELSE 0 END),0) jobs_reported_total,
              COALESCE(SUM(CASE WHEN kind='jobs' THEN fetched_count ELSE 0 END),0) jobs_fetched_total,
              MAX(finished_at) updated_at
            FROM scan_batches b
            WHERE b.id=(SELECT b2.id FROM scan_batches b2
              WHERE b2.source_slug=b.source_slug AND b2.kind=b.kind
              ORDER BY b2.started_at DESC, b2.rowid DESC LIMIT 1)
            """
        ).fetchone()
        open_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='open'").fetchone()[0]
        salary_jobs = conn.execute(
            "SELECT COUNT(DISTINCT job_id) FROM salaries WHERE job_id IN (SELECT id FROM jobs WHERE status='open')"
        ).fetchone()[0]
        papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        identified = conn.execute(
            "SELECT COUNT(DISTINCT author_id) FROM paper_authors WHERE author_id IS NOT NULL"
        ).fetchone()[0]
        all_authors = conn.execute("SELECT COUNT(*) FROM paper_authors").fetchone()[0]
        research = conn.execute(
            """
            SELECT COALESCE(SUM(matched_total),0), COALESCE(SUM(fetched_count),0),
              COALESCE(SUM(classified_count),0), COALESCE(SUM(relevant_count),0),
              COALESCE(SUM(pages_fetched),0), COALESCE(SUM(is_complete),0), COUNT(*),
              AVG(coverage_ratio)
            FROM research_queries
            WHERE batch_id=(
              SELECT b.id FROM scan_batches b
              WHERE b.kind='papers'
                AND EXISTS(SELECT 1 FROM research_queries q WHERE q.batch_id=b.id)
              ORDER BY b.started_at DESC, b.rowid DESC LIMIT 1
            )
            """
        ).fetchone()
        benchmark = benchmark_summary()
        return {
            "candidate_companies": totals["candidates"] or 0,
            "checked_companies": totals["checked"] or 0,
            "connected_companies": totals["connected"] or 0,
            "failed_companies": totals["failed"] or 0,
            "complete_scans": scans["complete_scans"] or 0,
            "partial_scans": scans["partial_scans"] or 0,
            "failed_scans": scans["failed_scans"] or 0,
            "reported_total": scans["jobs_reported_total"] or 0,
            "fetched_total": scans["jobs_fetched_total"] or 0,
            "updated_at": scans["updated_at"],
            "open_jobs": open_jobs,
            "salary_disclosure_rate": round(salary_jobs / open_jobs, 4) if open_jobs else None,
            "papers_stored": papers,
            "identified_authors": identified,
            "author_id_coverage": round(identified / all_authors, 4) if all_authors else None,
            "exchange_rate": current_usd_cny(),
            "research_retrieval": {
                "matched": research[0], "fetched": research[1],
                "classified": research[2], "relevant": research[3],
                "pages": research[4], "complete_directions": research[5],
                "directions": research[6],
                "average_coverage": round(research[7], 6) if research[7] is not None else None,
            },
            "automation": {
                "enabled": settings.auto_refresh_enabled,
                "refresh_hours": settings.auto_refresh_hours,
                "scheduler_check_minutes": settings.scheduler_check_minutes,
            },
            "benchmark": benchmark,
        }


def direction_table(
    market: str | None = None,
    city: str | None = None,
    education: str | None = None,
    experience: str | None = None,
    recruitment_type: str | None = None,
) -> list[dict[str, Any]]:
    job_clauses: list[str] = []
    params: list[str] = []
    for value, clause in [
        (market, "j.market=?"),
        (education, "j.education=?"),
        (experience, "j.experience=?"),
        (recruitment_type, "j.recruitment_type=?"),
    ]:
        if value:
            job_clauses.append(clause)
            params.append(value)
    if city:
        job_clauses.append("j.location LIKE ?")
        params.append(f"%{city}%")
    job_filter = "".join(f" AND {clause}" for clause in job_clauses)
    fx = current_usd_cny()
    usd_cny = float(fx["rate"])
    with connect() as conn:
        directions = rows(conn, "SELECT slug, name_zh, dimension, description FROM directions ORDER BY dimension, name_zh")
        result: list[dict[str, Any]] = []
        for direction in directions:
            slug = direction["slug"]
            job_rows = rows(
                conn,
                f"""
                SELECT j.id, j.company, j.headcount, j.recruitment_type
                FROM jobs j JOIN job_directions d ON d.job_id=j.id
                WHERE d.direction_slug=? AND d.relevance='core' AND j.status='open' {job_filter}
                """,
                (slug, *params),
            )
            possible_jobs = conn.execute(
                f"""
                SELECT COUNT(DISTINCT j.id) FROM jobs j
                JOIN job_directions d ON d.job_id=j.id
                WHERE d.direction_slug=? AND d.relevance='secondary' AND j.status='open' {job_filter}
                """,
                (slug, *params),
            ).fetchone()[0]
            talent_program_jobs = conn.execute(
                """
                SELECT COUNT(DISTINCT j.id) FROM jobs j
                JOIN job_directions d ON d.job_id=j.id
                WHERE d.direction_slug=? AND d.relevance='core'
                  AND j.source_kind='peer_benchmark' AND j.status='reference'
                """,
                (slug,),
            ).fetchone()[0]
            talent_program_possible = conn.execute(
                """
                SELECT COUNT(DISTINCT j.id) FROM jobs j
                JOIN job_directions d ON d.job_id=j.id
                WHERE d.direction_slug=? AND d.relevance='secondary'
                  AND j.source_kind='peer_benchmark' AND j.status='reference'
                """,
                (slug,),
            ).fetchone()[0]
            companies = Counter(row["company"] for row in job_rows)
            salary_values = [
                row[0] for row in conn.execute(
                    f"""
                    SELECT CASE s.currency
                      WHEN 'CNY' THEN (s.annual_lower+s.annual_upper)/2.0
                      WHEN 'USD' THEN (s.annual_lower+s.annual_upper)/2.0 * ?
                    END
                    FROM salaries s
                    JOIN jobs j ON j.id=s.job_id JOIN job_directions d ON d.job_id=j.id
                    WHERE d.direction_slug=? AND d.relevance='core' AND j.status='open' AND s.currency IN ('USD','CNY')
                      AND s.annual_lower IS NOT NULL {job_filter}
                    """,
                    (usd_cny, slug, *params),
                ).fetchall()
                if row[0] is not None
            ]
            paper_count = conn.execute(
                "SELECT COUNT(DISTINCT paper_id) FROM paper_directions WHERE direction_slug=?",
                (slug,),
            ).fetchone()[0]
            recent_start = date.today() - relativedelta(years=1)
            prior_start = date.today() - relativedelta(years=2)
            recent_papers = conn.execute(
                """SELECT COUNT(DISTINCT p.id) FROM papers p JOIN paper_directions d ON d.paper_id=p.id
                WHERE d.direction_slug=? AND p.earliest_public_date>=?""",
                (slug, recent_start.isoformat()),
            ).fetchone()[0]
            prior_papers = conn.execute(
                """SELECT COUNT(DISTINCT p.id) FROM papers p JOIN paper_directions d ON d.paper_id=p.id
                WHERE d.direction_slug=? AND p.earliest_public_date>=? AND p.earliest_public_date<?""",
                (slug, prior_start.isoformat(), recent_start.isoformat()),
            ).fetchone()[0]
            paper_growth = (recent_papers - prior_papers) / prior_papers if prior_papers else None
            author_count = conn.execute(
                """
                SELECT COUNT(DISTINCT a.author_id) FROM paper_authors a
                JOIN paper_directions d ON d.paper_id=a.paper_id
                WHERE d.direction_slug=? AND a.author_id IS NOT NULL
                """,
                (slug,),
            ).fetchone()[0]
            institution_count = conn.execute(
                """
                SELECT COUNT(DISTINCT a.institution_id) FROM paper_authors a
                JOIN paper_directions d ON d.paper_id=a.paper_id
                WHERE d.direction_slug=? AND a.institution_id IS NOT NULL
                """,
                (slug,),
            ).fetchone()[0]
            query_row = conn.execute(
                """
                SELECT matched_total, fetched_count, relevant_count, is_complete,
                  pages_fetched, coverage_ratio, query_strategy, year_start, year_end, error
                FROM research_queries WHERE direction_slug=? ORDER BY id DESC LIMIT 1
                """,
                (slug,),
            ).fetchone()
            salary_summary = None
            if len(salary_values) >= settings.min_salary_sample:
                salary_summary = {
                    "currency": "CNY", "unit": "year", "n": len(salary_values),
                    "p25": percentile(salary_values, .25), "median": median(salary_values),
                    "p75": percentile(salary_values, .75),
                    "rate": usd_cny,
                    "rate_date": fx["effective_date"],
                    "note": "统一折算成人民币年薪；这是招聘广告区间的中点，不是实际到手收入",
                }
            known_headcount = sum(max(0, int(row["headcount"] or 0)) for row in job_rows)
            headcount_jobs = sum(row["headcount"] is not None for row in job_rows)
            early_career_jobs = sum(row["recruitment_type"] in {"campus", "internship"} for row in job_rows)
            result.append(
                {
                    **direction,
                    "open_jobs": len(job_rows),
                    "possible_jobs": possible_jobs,
                    "talent_program_jobs": talent_program_jobs,
                    "talent_program_possible": talent_program_possible,
                    "employers": len(companies),
                    "top_employer_share": round(max(companies.values()) / len(job_rows), 3) if job_rows else None,
                    "salary_comparable_n": len(salary_values),
                    "salary_summary": salary_summary,
                    "known_headcount": known_headcount,
                    "headcount_jobs": headcount_jobs,
                    "early_career_jobs": early_career_jobs,
                    "papers": paper_count,
                    "papers_recent_12m": recent_papers,
                    "papers_prior_12m": prior_papers,
                    "paper_growth": round(paper_growth, 4) if paper_growth is not None else None,
                    "active_authors": author_count,
                    "institutions": institution_count,
                    "research_scope": dict(query_row) if query_row else None,
                }
            )
        return attach_value_scores(result, personal_difficulty_by_direction())


def _month_range(start: date, end: date) -> list[str]:
    values: list[str] = []
    cursor = start.replace(day=1)
    while cursor <= end:
        values.append(cursor.strftime("%Y-%m"))
        cursor += relativedelta(months=1)
    return values


def direction_detail(slug: str) -> dict[str, Any] | None:
    usd_cny = float(current_usd_cny()["rate"])
    with connect() as conn:
        direction = conn.execute("SELECT * FROM directions WHERE slug=?", (slug,)).fetchone()
        if not direction:
            return None
        start = date.today() - relativedelta(years=settings.paper_years)
        months = _month_range(start, date.today())
        monthly_rows = conn.execute(
            """
            SELECT substr(p.earliest_public_date,1,7) month, COUNT(DISTINCT p.id) papers
            FROM papers p JOIN paper_directions d ON d.paper_id=p.id
            WHERE d.direction_slug=? AND p.earliest_public_date>=?
            GROUP BY month ORDER BY month
            """,
            (slug, start.isoformat()),
        ).fetchall()
        monthly_map = {row[0]: row[1] for row in monthly_rows}
        paper_series = [{"date": month, "value": monthly_map.get(month, 0)} for month in months]
        # Rolling 12-month author count uses a set union over papers in the full window.
        author_series = []
        for month in months:
            month_date = datetime.strptime(month + "-01", "%Y-%m-%d").date()
            window_start = month_date - relativedelta(months=11)
            window_end = month_date + relativedelta(months=1)
            count = conn.execute(
                """
                SELECT COUNT(DISTINCT a.author_id) FROM paper_authors a
                JOIN papers p ON p.id=a.paper_id JOIN paper_directions d ON d.paper_id=p.id
                WHERE d.direction_slug=? AND a.author_id IS NOT NULL
                  AND p.earliest_public_date>=? AND p.earliest_public_date<?
                """,
                (slug, window_start.isoformat(), window_end.isoformat()),
            ).fetchone()[0]
            author_series.append({"date": month, "value": count})
        job_snapshots = rows(
            conn,
            "SELECT captured_at date, value, completeness, sample_size FROM snapshots WHERE metric='open_jobs' AND scope_key=? ORDER BY captured_at",
            (slug,),
        )
        query = conn.execute(
            "SELECT * FROM research_queries WHERE direction_slug=? ORDER BY id DESC LIMIT 1", (slug,)
        ).fetchone()
        stable_research = bool(query and query["is_complete"])
        # The current month is unfinished, so it is excluded from the forecast input.
        paper_forecast = forecast_series(
            [item["value"] for item in paper_series[:-1]],
            stable=stable_research,
            allow_exploratory=True,
        )
        forecast_months: list[str] = []
        if paper_forecast.values:
            cursor = date.today().replace(day=1)
            for step in range(1, len(paper_forecast.values) + 1):
                forecast_months.append((cursor + relativedelta(months=step)).strftime("%Y-%m"))
        jobs = rows(
            conn,
            """
            SELECT j.id, j.company, j.title, j.location, j.work_nature, j.education,
              j.experience, j.salary_raw, j.url, j.last_seen_at,
              MIN(CASE s.currency WHEN 'CNY' THEN s.lower_value WHEN 'USD' THEN s.lower_value * ? END) salary_cny_lower,
              MAX(CASE s.currency WHEN 'CNY' THEN s.upper_value WHEN 'USD' THEN s.upper_value * ? END) salary_cny_upper,
              MIN(CASE s.currency WHEN 'CNY' THEN s.annual_lower WHEN 'USD' THEN s.annual_lower * ? END) annual_cny_lower,
              MAX(CASE s.currency WHEN 'CNY' THEN s.annual_upper WHEN 'USD' THEN s.annual_upper * ? END) annual_cny_upper,
              MAX(s.currency) original_currency, MAX(s.period) salary_period
            FROM jobs j JOIN job_directions d ON d.job_id=j.id
            LEFT JOIN salaries s ON s.job_id=j.id
            WHERE d.direction_slug=? AND d.relevance='core' AND j.status='open'
            GROUP BY j.id ORDER BY j.last_seen_at DESC LIMIT 12
            """,
            (usd_cny, usd_cny, usd_cny, usd_cny, slug),
        )
        papers = rows(
            conn,
            """
            SELECT p.id, p.title, p.earliest_public_date, p.source_url,
              group_concat(DISTINCT a.display_name) authors
            FROM papers p JOIN paper_directions d ON d.paper_id=p.id
            LEFT JOIN paper_authors a ON a.paper_id=p.id
            WHERE d.direction_slug=? GROUP BY p.id ORDER BY p.earliest_public_date DESC LIMIT 12
            """,
            (slug,),
        )
        return {
            "direction": dict(direction),
            "paper_monthly": paper_series,
            "authors_rolling_12m": author_series,
            "job_snapshots": job_snapshots,
            "forecast": {**paper_forecast.__dict__, "dates": forecast_months},
            "scope": dict(query) if query else None,
            "jobs": jobs,
            "papers": papers,
            "limitations": [
                "岗位数按公开招聘页面计算；只有页面明确写了人数，才会计入招聘人数。",
                "作者人数按同一作者 ID 去重，用来观察研究热度，不等于求职人数。",
                "论文只统计已经抓取并判定相关的部分，热度预测因此属于低置信度参考。",
                "岗位趋势会从每次更新开始积累；历史还短时只展示当前需求，不硬猜涨跌。",
            ],
        }


def source_ledger() -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(
            conn,
            """
            SELECT s.*, b.status last_scan_status, b.reported_total, b.fetched_count,
              b.relevant_count, b.finished_at last_scan_at, b.error scan_error
            FROM sources s LEFT JOIN scan_batches b ON b.id=(
              SELECT id FROM scan_batches WHERE source_slug=s.slug ORDER BY started_at DESC LIMIT 1
            ) ORDER BY CASE s.status WHEN 'connected' THEN 0 WHEN 'accessible' THEN 1
              WHEN 'partial' THEN 2 WHEN 'failed' THEN 3 ELSE 4 END, s.market, s.company
            """,
        )


def paper_source_ledger() -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(
            conn,
            """
            SELECT source_slug, status, reported_total, fetched_count, relevant_count,
              finished_at, error, raw_path
            FROM scan_batches b
            WHERE kind='papers' AND id=(
              SELECT id FROM scan_batches b2 WHERE b2.kind='papers' AND b2.source_slug=b.source_slug
              ORDER BY started_at DESC, rowid DESC LIMIT 1
            )
            ORDER BY source_slug
            """,
        )
