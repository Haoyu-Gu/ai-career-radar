from __future__ import annotations

import re
from typing import Any

from ..db import json_dumps, utcnow


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().casefold()
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized)
    normalized = re.sub(r"^doi:\s*", "", normalized)
    return normalized or None


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value.casefold()).strip()


def upsert_paper(conn, record: dict[str, Any], source: str) -> tuple[int, bool]:
    """Merge OpenAlex/arXiv/Crossref identities into one canonical paper row."""
    openalex_id = record.get("openalex_id")
    arxiv_id = record.get("arxiv_id")
    doi = normalize_doi(record.get("doi"))
    title = (record.get("title") or "Untitled").strip()
    year = str(record.get("earliest_public_date") or record.get("formal_publication_date") or "")[:4]

    existing = None
    if openalex_id:
        existing = conn.execute("SELECT id FROM papers WHERE openalex_id=?", (openalex_id,)).fetchone()
    if not existing and arxiv_id:
        existing = conn.execute("SELECT id FROM papers WHERE arxiv_id=?", (arxiv_id,)).fetchone()
    if not existing and doi:
        existing = conn.execute("SELECT id FROM papers WHERE lower(doi)=?", (doi,)).fetchone()
    if not existing and year:
        # Title fallback is intentionally conservative and requires the same publication year.
        candidates = conn.execute(
            """SELECT id, title, openalex_id, arxiv_id, doi FROM papers
            WHERE substr(COALESCE(earliest_public_date, formal_publication_date),1,4)=?""",
            (year,),
        ).fetchall()
        target = normalize_title(title)
        # Exact-title fallback only joins records whose known identifiers do not
        # conflict. Generic titles can legitimately have many distinct DOIs.
        existing = next((
            item for item in candidates
            if normalize_title(item["title"]) == target
            and not (doi and item["doi"] and doi != normalize_doi(item["doi"]))
            and not (openalex_id and item["openalex_id"] and openalex_id != item["openalex_id"])
            and not (arxiv_id and item["arxiv_id"] and arxiv_id != item["arxiv_id"])
        ), None)

    raw_json = json_dumps(record.get("raw") or record)
    if existing:
        paper_id = int(existing["id"])
        conn.execute(
            """
            UPDATE papers SET
              openalex_id=COALESCE(openalex_id, ?), arxiv_id=COALESCE(arxiv_id, ?),
              doi=COALESCE(doi, ?), title=?, abstract=CASE WHEN length(COALESCE(?,'')) > length(COALESCE(abstract,'')) THEN ? ELSE abstract END,
              earliest_public_date=CASE
                WHEN earliest_public_date IS NULL THEN ?
                WHEN ? IS NOT NULL AND ? < earliest_public_date THEN ? ELSE earliest_public_date END,
              formal_publication_date=COALESCE(?, formal_publication_date),
              work_type=COALESCE(?, work_type), source_url=COALESCE(?, source_url), raw_json=?
            WHERE id=?
            """,
            (
                openalex_id, arxiv_id, doi, title, record.get("abstract"), record.get("abstract"),
                record.get("earliest_public_date"), record.get("earliest_public_date"),
                record.get("earliest_public_date"), record.get("earliest_public_date"),
                record.get("formal_publication_date"), record.get("work_type"),
                record.get("source_url"), raw_json, paper_id,
            ),
        )
        added = False
    else:
        cursor = conn.execute(
            """
            INSERT INTO papers(openalex_id, arxiv_id, doi, title, abstract, earliest_public_date,
              formal_publication_date, work_type, source_url, raw_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                openalex_id, arxiv_id, doi, title, record.get("abstract"),
                record.get("earliest_public_date"), record.get("formal_publication_date"),
                record.get("work_type"), record.get("source_url") or "", raw_json,
            ),
        )
        paper_id = int(cursor.lastrowid)
        added = True

    external_id = str(openalex_id or arxiv_id or doi or f"title:{normalize_title(title)}:{year}")
    conn.execute(
        """
        INSERT OR REPLACE INTO paper_sources(paper_id, source, external_id, source_url, observed_at)
        VALUES(?, ?, ?, ?, ?)
        """,
        (paper_id, source, external_id, record.get("source_url"), utcnow()),
    )
    return paper_id, added
