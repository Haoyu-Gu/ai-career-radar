import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .settings import settings


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  company TEXT NOT NULL,
  group_name TEXT,
  market TEXT NOT NULL,
  homepage TEXT,
  careers_url TEXT,
  adapter TEXT NOT NULL,
  namespace TEXT NOT NULL,
  token TEXT,
  status TEXT NOT NULL DEFAULT 'unchecked',
  robots_status TEXT,
  last_verified_at TEXT,
  last_success_at TEXT,
  last_error TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS scan_batches (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  source_slug TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  reported_total INTEGER,
  fetched_count INTEGER NOT NULL DEFAULT 0,
  relevant_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  raw_path TEXT,
  cursor TEXT,
  is_baseline INTEGER NOT NULL DEFAULT 0,
  config_version TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  source_namespace TEXT NOT NULL,
  source_slug TEXT NOT NULL,
  company TEXT NOT NULL,
  company_group TEXT,
  market TEXT NOT NULL,
  external_id TEXT NOT NULL,
  title TEXT NOT NULL,
  department TEXT,
  location TEXT,
  recruitment_type TEXT NOT NULL DEFAULT 'unknown',
  work_nature TEXT NOT NULL DEFAULT 'unknown',
  education TEXT NOT NULL DEFAULT 'unknown',
  experience TEXT NOT NULL DEFAULT 'unknown',
  responsibilities TEXT,
  requirements TEXT,
  benefits_raw TEXT,
  salary_raw TEXT,
  url TEXT NOT NULL,
  published_at TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  headcount INTEGER,
  content_hash TEXT NOT NULL,
  raw_json TEXT,
  source_kind TEXT NOT NULL DEFAULT 'live',
  talent_program TEXT,
  job_category TEXT,
  business_group TEXT,
  UNIQUE(source_namespace, company, external_id)
);

CREATE TABLE IF NOT EXISTS job_observations (
  batch_id TEXT NOT NULL REFERENCES scan_batches(id),
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  observed_at TEXT NOT NULL,
  PRIMARY KEY(batch_id, job_id)
);

CREATE TABLE IF NOT EXISTS salaries (
  id INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  lower_value REAL,
  upper_value REAL,
  currency TEXT,
  period TEXT,
  pay_months REAL,
  salary_type TEXT NOT NULL DEFAULT 'unknown',
  annual_lower REAL,
  annual_upper REAL,
  evidence_class TEXT NOT NULL DEFAULT 'A',
  city TEXT,
  level TEXT,
  evidence_text TEXT NOT NULL,
  source_url TEXT NOT NULL,
  UNIQUE(job_id, evidence_text)
);

CREATE TABLE IF NOT EXISTS exchange_rates (
  id INTEGER PRIMARY KEY,
  base_currency TEXT NOT NULL,
  quote_currency TEXT NOT NULL,
  rate REAL NOT NULL,
  effective_date TEXT NOT NULL,
  retrieved_at TEXT NOT NULL,
  source_url TEXT NOT NULL,
  UNIQUE(base_currency, quote_currency, effective_date)
);

CREATE TABLE IF NOT EXISTS directions (
  slug TEXT PRIMARY KEY,
  name_zh TEXT NOT NULL,
  dimension TEXT NOT NULL,
  description TEXT,
  query TEXT,
  config_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_directions (
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  direction_slug TEXT NOT NULL REFERENCES directions(slug),
  relevance TEXT NOT NULL,
  confidence REAL NOT NULL,
  rationale TEXT,
  classifier TEXT NOT NULL,
  version TEXT NOT NULL,
  PRIMARY KEY(job_id, direction_slug, version)
);

CREATE TABLE IF NOT EXISTS papers (
  id INTEGER PRIMARY KEY,
  openalex_id TEXT UNIQUE,
  arxiv_id TEXT,
  doi TEXT,
  title TEXT NOT NULL,
  abstract TEXT,
  earliest_public_date TEXT,
  formal_publication_date TEXT,
  date_precision TEXT NOT NULL DEFAULT 'day',
  work_type TEXT,
  source_url TEXT NOT NULL,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS paper_authors (
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  author_id TEXT,
  display_name TEXT NOT NULL,
  institution_id TEXT,
  institution_name TEXT,
  disambiguation_status TEXT NOT NULL DEFAULT 'identified',
  PRIMARY KEY(paper_id, author_id, display_name)
);

CREATE TABLE IF NOT EXISTS paper_directions (
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  direction_slug TEXT NOT NULL REFERENCES directions(slug),
  relevance TEXT NOT NULL,
  confidence REAL NOT NULL,
  rationale TEXT,
  classifier TEXT NOT NULL,
  version TEXT NOT NULL,
  PRIMARY KEY(paper_id, direction_slug, version)
);

CREATE TABLE IF NOT EXISTS paper_sources (
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  source_url TEXT,
  observed_at TEXT NOT NULL,
  PRIMARY KEY(paper_id, source, external_id)
);

CREATE TABLE IF NOT EXISTS research_queries (
  id INTEGER PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES scan_batches(id),
  direction_slug TEXT NOT NULL,
  matched_total INTEGER,
  fetched_count INTEGER NOT NULL,
  classified_count INTEGER NOT NULL,
  relevant_count INTEGER NOT NULL,
  is_complete INTEGER NOT NULL,
  api_cost_usd REAL,
  UNIQUE(batch_id, direction_slug)
);

CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY,
  captured_at TEXT NOT NULL,
  metric TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  value REAL,
  sample_size INTEGER,
  completeness TEXT NOT NULL,
  source_note TEXT,
  config_version TEXT,
  UNIQUE(captured_at, metric, scope_key)
);

CREATE TABLE IF NOT EXISTS refresh_runs (
  id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  phase TEXT NOT NULL,
  progress REAL NOT NULL DEFAULT 0,
  message TEXT,
  jobs_added INTEGER NOT NULL DEFAULT 0,
  papers_added INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS llm_cache (
  cache_key TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  response_json TEXT NOT NULL,
  tokens_used INTEGER
);

CREATE INDEX IF NOT EXISTS idx_jobs_market_status ON jobs(market, status);
CREATE INDEX IF NOT EXISTS idx_jobs_company_location ON jobs(company, location);
CREATE INDEX IF NOT EXISTS idx_job_directions_slug_job ON job_directions(direction_slug, job_id);
CREATE INDEX IF NOT EXISTS idx_papers_public_date ON papers(earliest_public_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_arxiv_id ON papers(arxiv_id) WHERE arxiv_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_paper_directions_slug_paper ON paper_directions(direction_slug, paper_id);
CREATE INDEX IF NOT EXISTS idx_paper_sources_external ON paper_sources(source, external_id);
CREATE INDEX IF NOT EXISTS idx_authors_author_paper ON paper_authors(author_id, paper_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_metric_scope_date ON snapshots(metric, scope_key, captured_at);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        # SQLite does not apply new CREATE TABLE columns to an existing local DB.
        # Keep upgrades additive so a personal dashboard can update in place.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        for name, definition in {
            "source_kind": "TEXT NOT NULL DEFAULT 'live'",
            "talent_program": "TEXT",
            "job_category": "TEXT",
            "business_group": "TEXT",
        }.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source_kind_status ON jobs(source_kind, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_talent_program ON jobs(talent_program)")
        query_columns = {row[1] for row in conn.execute("PRAGMA table_info(research_queries)")}
        for name, definition in {
            "pages_fetched": "INTEGER NOT NULL DEFAULT 0",
            "coverage_ratio": "REAL",
            "query_strategy": "TEXT",
            "year_start": "INTEGER",
            "year_end": "INTEGER",
            "error": "TEXT",
        }.items():
            if name not in query_columns:
                conn.execute(f"ALTER TABLE research_queries ADD COLUMN {name} {definition}")
        conn.execute("PRAGMA optimize")


@contextmanager
def transaction(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        conn.execute("BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
