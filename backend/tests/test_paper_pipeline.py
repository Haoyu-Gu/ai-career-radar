import pytest

from app.collectors.openalex import OpenAlexCollector
from app.db import connect, init_db
from app.services.config_loader import sync_config
from app.services.papers import upsert_paper
from app.settings import settings


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "papers.db"))
    init_db()
    sync_config()
    yield


def test_cross_source_paper_dedup_uses_doi(isolated_db):
    with connect() as conn:
        first_id, first_added = upsert_paper(conn, {
            "openalex_id": "W1", "doi": "https://doi.org/10.1/demo", "title": "A Demo Paper",
            "earliest_public_date": "2026-01-02", "source_url": "https://openalex.org/W1",
        }, "openalex")
        second_id, second_added = upsert_paper(conn, {
            "arxiv_id": "2601.00001", "doi": "10.1/DEMO", "title": "A Demo Paper",
            "earliest_public_date": "2026-01-01", "source_url": "https://arxiv.org/abs/2601.00001",
        }, "arxiv-rss")
        assert first_added is True
        assert second_added is False
        assert second_id == first_id
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM paper_sources").fetchone()[0] == 2


def test_same_title_and_year_does_not_merge_conflicting_dois(isolated_db):
    with connect() as conn:
        first_id, _ = upsert_paper(conn, {
            "doi": "10.1/first", "title": "Natural Language Processing",
            "earliest_public_date": "2026-01-01",
        }, "crossref")
        second_id, second_added = upsert_paper(conn, {
            "doi": "10.1/second", "title": "Natural Language Processing",
            "earliest_public_date": "2026-05-01",
        }, "crossref")
        assert second_added is True
        assert second_id != first_id
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 2


class CursorClient:
    def __init__(self, *_args, **_kwargs): pass
    async def close(self): pass
    async def get_json(self, _url, params, **_kwargs):
        page = 0 if params["cursor"] == "*" else 1
        work_id = page + 1
        return ({
            "meta": {"count": 2, "next_cursor": "next" if page == 0 else None},
            "results": [{
                "id": f"https://openalex.org/W{work_id}", "doi": f"https://doi.org/10.1/{work_id}",
                "title": f"Chain of Thought Reasoning Model {work_id}",
                "abstract_inverted_index": {"reasoning": [0], "model": [1]},
                "publication_date": "2026-02-01", "type": "article", "authorships": [],
                "primary_location": {"landing_page_url": f"https://example.test/{work_id}"}, "ids": {},
            }],
        }, {}, False)


@pytest.mark.asyncio
async def test_openalex_uses_cursor_pages_and_records_coverage(isolated_db, monkeypatch):
    monkeypatch.setattr("app.collectors.openalex.CachedHttpClient", CursorClient)
    monkeypatch.setattr(settings, "paper_years", 1)
    monkeypatch.setattr(settings, "openalex_max_per_direction", 2)
    monkeypatch.setattr(settings, "openalex_max_per_year", 2)
    monkeypatch.setattr(settings, "openalex_page_size", 1)

    result = await OpenAlexCollector().collect(["reasoning"])

    assert result["fetched"] == 2
    with connect() as conn:
        query = conn.execute("SELECT * FROM research_queries").fetchone()
        assert query["pages_fetched"] == 2
        assert query["coverage_ratio"] == 1
        assert query["is_complete"] == 1
        assert query["query_strategy"] == "yearly_cursor_title_abstract_v3"
