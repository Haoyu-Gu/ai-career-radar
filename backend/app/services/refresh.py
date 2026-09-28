import asyncio
import uuid
from collections.abc import Callable

from ..collectors.arxiv import ArxivCollector
from ..collectors.crossref import CrossrefCollector
from ..collectors.jobs import JobCollector
from ..collectors.openalex import OpenAlexCollector
from ..db import connect, utcnow
from ..settings import settings
from .metrics import snapshot_current_jobs
from .fx import refresh_usd_cny


_lock = asyncio.Lock()


def _update(run_id: str, phase: str, progress: float, message: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE refresh_runs SET phase=?, progress=?, message=? WHERE id=?",
            (phase, progress, message, run_id),
        )


async def run_refresh(run_id: str) -> None:
    if _lock.locked():
        with connect() as conn:
            conn.execute(
                "UPDATE refresh_runs SET status='failed', finished_at=?, message='已有刷新任务正在运行' WHERE id=?",
                (utcnow(), run_id),
            )
        return
    async with _lock:
        jobs_added = papers_added = 0
        try:
            _update(run_id, "汇率", .01, "正在更新美元兑人民币汇率")
            try:
                await refresh_usd_cny()
            except Exception:
                # A stale official rate is preferable to failing the entire data refresh.
                pass
            jobs = JobCollector(lambda p, x, m: _update(run_id, p, .03 + x * .27, m))
            job_result = await jobs.collect_all()
            jobs_added = job_result["added"]
            if settings.openalex_api_key:
                papers = OpenAlexCollector(lambda p, x, m: _update(run_id, p, .30 + x * .28, m))
                paper_result = await papers.collect()
                papers_added += paper_result["added"]
            else:
                _update(run_id, "论文主索引", .58, "未配置 OpenAlex API Key，自动使用 Crossref 公共索引")
            crossref = CrossrefCollector(lambda p, x, m: _update(run_id, p, .58 + x * .29, m))
            crossref_result = await crossref.collect()
            papers_added += crossref_result["added"]
            arxiv = ArxivCollector(lambda p, x, m: _update(run_id, p, .88 + x * .08, m))
            arxiv_result = await arxiv.collect()
            papers_added += arxiv_result["added"]
            snapshot_current_jobs()
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE refresh_runs SET status='complete', phase='完成', progress=1,
                      message='数据已保存并重算指标', finished_at=?, jobs_added=?, papers_added=? WHERE id=?
                    """,
                    (utcnow(), jobs_added, papers_added, run_id),
                )
        except Exception as exc:
            with connect() as conn:
                conn.execute(
                    "UPDATE refresh_runs SET status='failed', finished_at=?, message=? WHERE id=?",
                    (utcnow(), str(exc)[:1000], run_id),
                )


def create_refresh() -> str:
    run_id = f"refresh-{uuid.uuid4().hex[:12]}"
    with connect() as conn:
        conn.execute(
            "INSERT INTO refresh_runs(id, started_at, status, phase, progress, message) VALUES(?, ?, 'queued', '准备', 0, '等待启动')",
            (run_id, utcnow()),
        )
    return run_id
