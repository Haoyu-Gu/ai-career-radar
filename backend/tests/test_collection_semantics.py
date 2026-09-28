import sqlite3

import pytest

from app.collectors.jobs import JobCollector
from app.db import connect, init_db
from app.settings import settings
from app.services.config_loader import sync_config


SOURCE = {
    "slug": "test-ai",
    "company": "Test AI",
    "group": "Test AI",
    "market": "overseas_ai",
    "careers_url": "https://example.test/jobs",
    "adapter": "greenhouse",
    "namespace": "greenhouse",
    "token": "test-ai",
}


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.db"))
    init_db()
    sync_config()
    yield


class GoodClient:
    def __init__(self, *_args, **_kwargs): pass
    async def close(self): pass
    async def get_json(self, *_args, **_kwargs):
        return ({"jobs": [{
            "id": 42,
            "title": "Machine Learning Engineer",
            "location": {"name": "Shanghai"},
            "content": "Build large language model systems. Master's degree. 3 years experience.",
            "absolute_url": "https://example.test/jobs/42",
            "updated_at": "2026-09-01T00:00:00Z",
        }], "meta": {"total": 1}}, {}, False)


class FailingClient:
    def __init__(self, *_args, **_kwargs): pass
    async def close(self): pass
    async def get_json(self, *_args, **_kwargs):
        raise RuntimeError("blocked")


class ManyClient:
    def __init__(self, *_args, **_kwargs): pass
    async def close(self): pass
    async def get_json(self, *_args, **_kwargs):
        jobs = [{
            "id": index, "title": f"Machine Learning Engineer {index}",
            "location": {"name": "Shanghai"}, "content": "Build large language model systems.",
            "absolute_url": f"https://example.test/jobs/{index}", "updated_at": "2026-09-01T00:00:00Z",
        } for index in range(20)]
        return ({"jobs": jobs, "meta": {"total": 20}}, {}, False)


class SuspiciousDropClient:
    def __init__(self, *_args, **_kwargs): pass
    async def close(self): pass
    async def get_json(self, *_args, **_kwargs):
        jobs = [{
            "id": index, "title": f"Machine Learning Engineer {index}",
            "location": {"name": "Shanghai"}, "content": "Build large language model systems.",
            "absolute_url": f"https://example.test/jobs/{index}", "updated_at": "2026-09-02T00:00:00Z",
        } for index in range(2)]
        return ({"jobs": jobs, "meta": {"total": 2}}, {}, False)


@pytest.mark.asyncio
async def test_repeated_complete_scan_does_not_duplicate_jobs(isolated_db, monkeypatch):
    monkeypatch.setattr("app.collectors.jobs.CachedHttpClient", GoodClient)
    collector = JobCollector()
    await collector.collect_source(SOURCE, "test")
    await collector.collect_source(SOURCE, "test")
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM job_observations").fetchone()[0] == 2
        baseline_flags = [row[0] for row in conn.execute("SELECT is_baseline FROM scan_batches ORDER BY started_at, rowid")]
        assert baseline_flags == [1, 0]


@pytest.mark.asyncio
async def test_failed_scan_never_closes_existing_job(isolated_db, monkeypatch):
    monkeypatch.setattr("app.collectors.jobs.CachedHttpClient", GoodClient)
    await JobCollector().collect_source(SOURCE, "test")
    monkeypatch.setattr("app.collectors.jobs.CachedHttpClient", FailingClient)
    result = await JobCollector().collect_source(SOURCE, "test")
    assert result["status"] == "failed"
    with connect() as conn:
        assert conn.execute("SELECT status FROM jobs WHERE external_id='42'").fetchone()[0] == "open"


@pytest.mark.asyncio
async def test_suspicious_job_drop_is_partial_and_preserves_old_jobs(isolated_db, monkeypatch):
    monkeypatch.setattr("app.collectors.jobs.CachedHttpClient", ManyClient)
    await JobCollector().collect_source(SOURCE, "test")
    monkeypatch.setattr("app.collectors.jobs.CachedHttpClient", SuspiciousDropClient)

    result = await JobCollector().collect_source(SOURCE, "test")

    assert result["status"] == "partial"
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE status='open'").fetchone()[0] == 20
        error = conn.execute("SELECT error FROM scan_batches ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        assert "异常下降保护" in error
