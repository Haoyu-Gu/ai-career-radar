from __future__ import annotations

import json
import re
import uuid
from datetime import date
from html import unescape
from typing import Any, Callable

from ..db import connect, utcnow
from ..services.classify import direction_matches
from ..services.config_loader import load_directions
from ..services.papers import upsert_paper
from ..settings import settings
from .http import CachedHttpClient


Progress = Callable[[str, float, str], None]


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value or ""))).strip()


def _date_from_parts(item: dict[str, Any]) -> str | None:
    for key in ("published-print", "published-online", "published", "issued"):
        parts = ((item.get(key) or {}).get("date-parts") or [[]])[0]
        if parts:
            year = int(parts[0])
            month = int(parts[1]) if len(parts) > 1 else 1
            day = int(parts[2]) if len(parts) > 2 else 1
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None


class CrossrefCollector:
    """Keyless DOI metadata fallback and enrichment with cursor pagination."""

    def __init__(self, progress: Progress | None = None):
        self.progress = progress or (lambda *_: None)

    async def collect(self, selected: list[str] | None = None) -> dict[str, int]:
        doc = load_directions()
        directions = [item for item in doc["directions"] if not selected or item["slug"] in selected]
        batch_id = f"crossref-{uuid.uuid4().hex[:12]}"
        with connect() as conn:
            conn.execute(
                "INSERT INTO scan_batches(id, kind, source_slug, started_at, status, config_version) VALUES(?, 'papers', 'crossref', ?, 'running', ?)",
                (batch_id, utcnow(), doc["version"]),
            )
        settings.raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = settings.raw_dir / f"{batch_id}.jsonl"
        client = CachedHttpClient("crossref", min_interval=0.35)
        added = fetched_total = relevant_total = reported_total = pages_total = 0
        failures: list[str] = []
        current_year = date.today().year
        first_year = current_year - settings.paper_years + 1
        years = range(first_year, current_year + 1)
        try:
            with raw_path.open("w", encoding="utf-8") as raw_file:
                for direction_index, direction in enumerate(directions):
                    direction_fetched = direction_relevant = direction_matched = direction_pages = 0
                    direction_failures: list[str] = []
                    direction_complete = True
                    # Crossref's title query preserves the quoted OR phrases from the
                    # direction config. A bag-of-words bibliographic query is far too
                    # broad (for example, "language" also retrieves linguistics books).
                    query = direction.get("query") or direction["name_zh"]
                    for year_index, year in enumerate(years):
                        if direction_fetched >= settings.crossref_max_per_direction:
                            direction_complete = False
                            break
                        self.progress(
                            "论文 DOI 补充",
                            (direction_index + year_index / settings.paper_years) / max(len(directions), 1),
                            f"{direction['name_zh']} · {year}",
                        )
                        year_cap = min(
                            settings.crossref_max_per_year,
                            settings.crossref_max_per_direction - direction_fetched,
                        )
                        cursor = "*"
                        year_fetched = year_matched = 0
                        while year_fetched < year_cap:
                            rows_requested = min(settings.crossref_page_size, year_cap - year_fetched, 1000)
                            params: dict[str, Any] = {
                                "query.title": query,
                                "filter": f"from-pub-date:{year}-01-01,until-pub-date:{year}-12-31",
                                "rows": rows_requested,
                                "cursor": cursor,
                                "sort": "relevance",
                                "order": "desc",
                                "select": "DOI,title,abstract,published,published-print,published-online,issued,author,URL,type,container-title",
                            }
                            if settings.crossref_mailto:
                                params["mailto"] = settings.crossref_mailto
                            try:
                                payload, _, cached = await client.get_json(
                                    "https://api.crossref.org/works", params=params, cache_ttl_seconds=86400
                                )
                            except Exception as exc:
                                message = f"{direction['slug']}:{year}: {exc}"
                                failures.append(message)
                                direction_failures.append(message)
                                direction_complete = False
                                break
                            message = payload.get("message") or {}
                            items = message.get("items") or []
                            if year_fetched == 0:
                                year_matched = int(message.get("total-results") or 0)
                                direction_matched += year_matched
                                reported_total += year_matched
                            raw_file.write(json.dumps({
                                "direction": direction["slug"], "year": year, "cursor": cursor,
                                "cached": cached, "response": payload,
                            }, ensure_ascii=False) + "\n")
                            raw_file.flush()
                            pages_total += 1
                            direction_pages += 1
                            if not items:
                                break
                            with connect() as conn:
                                for item in items:
                                    title = _clean(" ".join(item.get("title") or [])) or "Untitled"
                                    abstract = _clean(item.get("abstract"))
                                    published = _date_from_parts(item)
                                    doi = item.get("DOI")
                                    paper_id, was_added = upsert_paper(conn, {
                                        "doi": doi, "title": title, "abstract": abstract,
                                        "earliest_public_date": published, "formal_publication_date": published,
                                        "work_type": item.get("type"),
                                        "source_url": item.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
                                        "raw": item,
                                    }, "crossref")
                                    added += int(was_added)
                                    for author in item.get("author") or []:
                                        name = " ".join(filter(None, (author.get("given"), author.get("family")))).strip()
                                        if not name:
                                            continue
                                        orcid = (author.get("ORCID") or "").rstrip("/").rsplit("/", 1)[-1] or None
                                        affiliation = (author.get("affiliation") or [{}])[0].get("name")
                                        conn.execute(
                                            """INSERT OR IGNORE INTO paper_authors(paper_id, author_id, display_name,
                                              institution_name, disambiguation_status) VALUES(?, ?, ?, ?, ?)""",
                                            (paper_id, orcid, name, affiliation, "identified" if orcid else "unresolved"),
                                        )
                                    matches = direction_matches(f"{title}\n{abstract}")
                                    if any(match["slug"] == direction["slug"] for match in matches):
                                        direction_relevant += 1
                                        relevant_total += 1
                                    for match in matches:
                                        conn.execute(
                                            """INSERT OR REPLACE INTO paper_directions(paper_id, direction_slug, relevance,
                                              confidence, rationale, classifier, version)
                                            VALUES(?, ?, ?, ?, ?, 'rules', ?)""",
                                            (paper_id, match["slug"], match["relevance"], match["confidence"],
                                             match["rationale"], doc["version"]),
                                        )
                            count = len(items)
                            year_fetched += count
                            direction_fetched += count
                            fetched_total += count
                            next_cursor = message.get("next-cursor")
                            if count < rows_requested or not next_cursor or next_cursor == cursor:
                                break
                            cursor = next_cursor
                        if year_fetched < year_matched:
                            direction_complete = False

                    coverage = direction_fetched / direction_matched if direction_matched else None
                    with connect() as conn:
                        conn.execute(
                            """INSERT OR REPLACE INTO research_queries(
                              batch_id, direction_slug, matched_total, fetched_count, classified_count,
                              relevant_count, is_complete, pages_fetched, coverage_ratio, query_strategy,
                              year_start, year_end, error)
                            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'crossref_yearly_cursor_v1', ?, ?, ?)""",
                            (batch_id, direction["slug"], direction_matched, direction_fetched, direction_fetched,
                             direction_relevant, int(direction_complete), direction_pages,
                             round(coverage, 6) if coverage is not None else None,
                             first_year, current_year, "\n".join(direction_failures) or None),
                        )
        finally:
            await client.close()

        with connect() as conn:
            complete = conn.execute(
                "SELECT COUNT(*) FROM research_queries WHERE batch_id=? AND is_complete=1", (batch_id,)
            ).fetchone()[0]
            status = "complete" if complete == len(directions) and not failures else "partial"
            conn.execute(
                """UPDATE scan_batches SET finished_at=?, status=?, reported_total=?, fetched_count=?,
                  relevant_count=?, error=?, raw_path=? WHERE id=?""",
                (utcnow(), status, reported_total, fetched_total, relevant_total,
                 "\n".join(failures)[:4000] or None, str(raw_path), batch_id),
            )
        self.progress("论文 DOI 补充", 1, f"Crossref {pages_total} 页 / {fetched_total} 条")
        return {"added": added, "fetched": fetched_total, "relevant": relevant_total,
                "failures": len(failures), "complete_directions": complete, "directions": len(directions)}
