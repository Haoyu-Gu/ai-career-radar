import asyncio
import csv
import io
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .db import connect, init_db, rows
from .services.config_loader import sync_config
from .services.benchmark import benchmark_summary
from .services.explain import explain_direction
from .services.fx import current_usd_cny
from .services.factors import mine_jd_factors
from .services.metrics import coverage, direction_detail, direction_table, paper_source_ledger, source_ledger
from .services.refresh import create_refresh, run_refresh
from .services.scheduler import scheduler_loop, stop_scheduler
from .settings import ROOT, settings


active_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    sync_config()
    scheduler_task = asyncio.create_task(scheduler_loop()) if settings.auto_refresh_enabled else None
    try:
        yield
    finally:
        await stop_scheduler(scheduler_task)


app = FastAPI(title="AI Career Radar · AI 就业雷达", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/benchmark")
def benchmark() -> dict[str, Any]:
    return benchmark_summary()


@app.get("/api/jd-factors")
def jd_factors(source_kind: str = "peer_benchmark") -> dict[str, Any]:
    if source_kind not in {"peer_benchmark", "live"}:
        raise HTTPException(400, "source_kind 只支持 peer_benchmark 或 live")
    return mine_jd_factors(source_kind)


@app.get("/api/overview")
def overview(
    market: str | None = None,
    city: str | None = None,
    education: str | None = None,
    experience: str | None = None,
    recruitment_type: str | None = None,
) -> dict[str, Any]:
    return {
        "coverage": coverage(),
        "directions": direction_table(market, city, education, experience, recruitment_type),
    }


@app.get("/api/directions/{slug}")
def get_direction(slug: str) -> dict[str, Any]:
    result = direction_detail(slug)
    if not result:
        raise HTTPException(404, "方向不存在")
    return result


@app.post("/api/directions/{slug}/explain")
async def get_explanation(slug: str) -> dict[str, Any]:
    try:
        return await explain_direction(slug)
    except ValueError:
        raise HTTPException(404, "方向不存在")
    except Exception as exc:
        fallback = await asyncio.to_thread(__import__("app.services.explain", fromlist=["rule_summary"]).rule_summary, slug)
        fallback["llm_error"] = str(exc)[:300]
        return fallback


SORTS = {
    "updated": "j.last_seen_at DESC",
    "company": "j.company ASC, j.title ASC",
    "experience": "CASE j.experience WHEN '0-1y' THEN 0 WHEN '2-4y' THEN 1 WHEN '5-9y' THEN 2 WHEN '10y+' THEN 3 ELSE 4 END",
}


def _job_query(
    search: str | None, market: str | None, company: str | None, city: str | None,
    education: str | None, experience: str | None, recruitment_type: str | None,
    direction: str | None, status: str, sort: str,
) -> tuple[str, list[Any]]:
    joins = "LEFT JOIN salaries s ON s.job_id=j.id"
    clauses = ["j.status=?"]
    params: list[Any] = [status]
    if direction:
        joins += " JOIN job_directions jd ON jd.job_id=j.id"
        clauses.append("jd.direction_slug=?")
        params.append(direction)
        clauses.append("jd.relevance='core'")
    for value, clause in [
        (market, "j.market=?"), (company, "j.company=?"), (education, "j.education=?"),
        (experience, "j.experience=?"), (recruitment_type, "j.recruitment_type=?"),
    ]:
        if value:
            clauses.append(clause)
            params.append(value)
    if city:
        clauses.append("j.location LIKE ?")
        params.append(f"%{city}%")
    if search:
        clauses.append("(j.title LIKE ? OR j.department LIKE ? OR j.requirements LIKE ?)")
        params.extend([f"%{search}%"] * 3)
    usd_cny = float(current_usd_cny()["rate"])
    salary_sort = (
        "MAX(CASE s.currency WHEN 'CNY' THEN s.annual_upper "
        f"WHEN 'USD' THEN s.annual_upper * {usd_cny:.8f} END) DESC"
    )
    order_by = salary_sort if sort == "salary_high" else SORTS.get(sort, SORTS["updated"])
    sql = f"""
      FROM jobs j {joins}
      WHERE {' AND '.join(clauses)}
      GROUP BY j.id ORDER BY {order_by}
    """
    return sql, params


@app.get("/api/jobs")
def list_jobs(
    search: str | None = None, market: str | None = None, company: str | None = None,
    city: str | None = None, education: str | None = None, direction: str | None = None,
    experience: str | None = None, recruitment_type: str | None = None,
    status: str = "open", sort: str = "updated", limit: int = Query(50, ge=1, le=250),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    sql, params = _job_query(search, market, company, city, education, experience, recruitment_type, direction, status, sort)
    with connect() as conn:
        usd_cny = float(current_usd_cny()["rate"])
        total = conn.execute(f"SELECT COUNT(*) FROM (SELECT j.id {sql})", params).fetchone()[0]
        items = rows(
            conn,
            f"""
            SELECT j.id, j.company, j.market, j.title, j.department, j.location,
              j.recruitment_type, j.work_nature, j.education, j.experience, j.salary_raw,
              j.talent_program, j.job_category, j.business_group, j.source_kind,
              MIN(CASE s.currency WHEN 'CNY' THEN s.lower_value WHEN 'USD' THEN s.lower_value * ? END) salary_cny_lower,
              MAX(CASE s.currency WHEN 'CNY' THEN s.upper_value WHEN 'USD' THEN s.upper_value * ? END) salary_cny_upper,
              MIN(CASE s.currency WHEN 'CNY' THEN s.annual_lower WHEN 'USD' THEN s.annual_lower * ? END) annual_cny_lower,
              MAX(CASE s.currency WHEN 'CNY' THEN s.annual_upper WHEN 'USD' THEN s.annual_upper * ? END) annual_cny_upper,
              MAX(s.currency) original_currency, MAX(s.period) salary_period, j.url, j.published_at,
              j.first_seen_at, j.last_seen_at, j.status {sql} LIMIT ? OFFSET ?
            """,
            (usd_cny, usd_cny, usd_cny, usd_cny, *params, limit, offset),
        )
    return {
        "total": total, "items": items, "limit": limit, "offset": offset,
        "exchange_rate": current_usd_cny(),
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict[str, Any]:
    with connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise HTTPException(404, "岗位不存在")
        result = dict(job)
        result.pop("raw_json", None)
        result["salaries"] = rows(conn, "SELECT * FROM salaries WHERE job_id=?", (job_id,))
        result["directions"] = rows(
            conn,
            "SELECT d.direction_slug, x.name_zh, d.relevance, d.confidence, d.rationale FROM job_directions d JOIN directions x ON x.slug=d.direction_slug WHERE d.job_id=?",
            (job_id,),
        )
        return result


@app.get("/api/papers")
def list_papers(direction: str | None = None, limit: int = Query(50, ge=1, le=250), offset: int = 0) -> dict[str, Any]:
    join = "JOIN paper_directions d ON d.paper_id=p.id" if direction else ""
    where = "WHERE d.direction_slug=?" if direction else ""
    params = (direction,) if direction else ()
    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(DISTINCT p.id) FROM papers p {join} {where}", params).fetchone()[0]
        items = rows(
            conn,
            f"""
            SELECT p.id, p.title, p.earliest_public_date, p.work_type, p.source_url,
              group_concat(DISTINCT a.display_name) authors
            FROM papers p {join} LEFT JOIN paper_authors a ON a.paper_id=p.id {where}
            GROUP BY p.id ORDER BY p.earliest_public_date DESC LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        )
    return {"total": total, "items": items}


@app.get("/api/sources")
def sources() -> dict[str, Any]:
    return {"coverage": coverage(), "items": source_ledger(), "paper_sources": paper_source_ledger()}


@app.post("/api/refresh", status_code=202)
async def refresh() -> dict[str, str]:
    with connect() as conn:
        running = conn.execute("SELECT id FROM refresh_runs WHERE status IN ('queued','running') ORDER BY started_at DESC LIMIT 1").fetchone()
    if running:
        return {"run_id": running[0], "status": "already_running"}
    run_id = create_refresh()
    task = asyncio.create_task(run_refresh(run_id))
    active_tasks.add(task)
    task.add_done_callback(active_tasks.discard)
    return {"run_id": run_id, "status": "queued"}


@app.get("/api/refresh/{run_id}")
def refresh_status(run_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM refresh_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, "刷新任务不存在")
    return dict(row)


@app.get("/api/jobs.csv")
def export_jobs(
    search: str | None = None, market: str | None = None, company: str | None = None,
    city: str | None = None, education: str | None = None, direction: str | None = None,
    experience: str | None = None, recruitment_type: str | None = None,
) -> StreamingResponse:
    sql, params = _job_query(search, market, company, city, education, experience, recruitment_type, direction, "open", "updated")
    with connect() as conn:
        usd_cny = float(current_usd_cny()["rate"])
        items = rows(
            conn,
            f"""SELECT j.company,j.title,j.department,j.location,j.recruitment_type,
              j.work_nature,j.education,j.experience,j.salary_raw,
              j.talent_program,j.job_category,j.business_group,j.source_kind,
              MIN(CASE s.currency WHEN 'CNY' THEN s.annual_lower WHEN 'USD' THEN s.annual_lower * ? END) annual_cny_lower,
              MAX(CASE s.currency WHEN 'CNY' THEN s.annual_upper WHEN 'USD' THEN s.annual_upper * ? END) annual_cny_upper,
              j.url,j.last_seen_at {sql}""",
            (usd_cny, usd_cny, *params),
        )
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.DictWriter(output, fieldnames=list(items[0].keys()) if items else ["company", "title", "url"])
    writer.writeheader()
    writer.writerows(items)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=ai-jobs.csv"})


frontend_dist = ROOT / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        requested = frontend_dist / full_path
        if full_path and requested.exists() and requested.is_file():
            return FileResponse(requested)
        return FileResponse(frontend_dist / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=False)
