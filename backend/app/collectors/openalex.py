import json
import uuid
from datetime import date
from typing import Any, Callable

from ..db import connect, utcnow
from ..services.classify import direction_matches
from ..services.config_loader import load_directions
from ..services.papers import upsert_paper
from ..settings import settings
from .http import CachedHttpClient


Progress = Callable[[str, float, str], None]


def reconstruct_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    words: list[tuple[int, str]] = []
    for word, positions in index.items():
        words.extend((position, word) for position in positions)
    return " ".join(word for _, word in sorted(words))


def short_id(value: str | None) -> str | None:
    return value.rsplit("/", 1)[-1] if value else None


class OpenAlexCollector:
    """Year-stratified cursor collector with explicit, auditable coverage."""

    def __init__(self, progress: Progress | None = None):
        self.progress = progress or (lambda *_: None)

    async def collect(self, selected: list[str] | None = None) -> dict[str, int | float]:
        doc = load_directions()
        directions = [item for item in doc["directions"] if not selected or item["slug"] in selected]
        batch_id = f"openalex-{uuid.uuid4().hex[:12]}"
        with connect() as conn:
            conn.execute(
                "INSERT INTO scan_batches(id, kind, source_slug, started_at, status, config_version) VALUES(?, 'papers', 'openalex', ?, 'running', ?)",
                (batch_id, utcnow(), doc["version"]),
            )

        settings.raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = settings.raw_dir / f"{batch_id}.jsonl"
        client = CachedHttpClient("openalex-v2", min_interval=0.7)
        added = fetched_total = relevant_total = reported_total = pages_total = 0
        failures: list[str] = []
        current_year = date.today().year
        first_year = current_year - settings.paper_years + 1
        years = list(range(first_year, current_year + 1))

        try:
            with raw_path.open("w", encoding="utf-8") as raw_file:
                for direction_index, direction in enumerate(directions):
                    direction_fetched = direction_relevant = direction_matched = direction_pages = 0
                    direction_failures: list[str] = []
                    direction_complete = True
                    for year_index, year in enumerate(years):
                        if direction_fetched >= settings.openalex_max_per_direction:
                            direction_complete = False
                            break
                        progress = (direction_index + year_index / max(len(years), 1)) / max(len(directions), 1)
                        self.progress("论文数据", progress, f"{direction['name_zh']} · {year} · 已取 {direction_fetched} 条")
                        per_year_cap = min(
                            settings.openalex_max_per_year,
                            settings.openalex_max_per_direction - direction_fetched,
                        )
                        cursor = "*"
                        year_fetched = year_matched = 0
                        year_complete = True
                        while year_fetched < per_year_cap:
                            page_size = min(settings.openalex_page_size, per_year_cap - year_fetched, 200)
                            params: dict[str, Any] = {
                                "filter": (
                                    f"from_publication_date:{year}-01-01,to_publication_date:{year}-12-31,"
                                    f"title_and_abstract.search:{direction['query']}"
                                ),
                                "per_page": page_size,
                                "cursor": cursor,
                                "select": "id,doi,title,abstract_inverted_index,publication_date,type,authorships,primary_location,ids,primary_topic,cited_by_count",
                            }
                            if settings.openalex_api_key:
                                params["api_key"] = settings.openalex_api_key
                            try:
                                payload, _, cached = await client.get_json(
                                    "https://api.openalex.org/works", params=params, cache_ttl_seconds=86400
                                )
                            except Exception as exc:
                                message = f"{direction['slug']}:{year}: {exc}"
                                failures.append(message)
                                direction_failures.append(message)
                                year_complete = direction_complete = False
                                break

                            results = payload.get("results") or []
                            meta = payload.get("meta") or {}
                            if year_fetched == 0:
                                year_matched = int(meta.get("count") or 0)
                                direction_matched += year_matched
                                reported_total += year_matched
                            raw_file.write(json.dumps({
                                "direction": direction["slug"], "year": year, "cursor": cursor,
                                "cached": cached, "response": payload,
                            }, ensure_ascii=False) + "\n")
                            raw_file.flush()
                            direction_pages += 1
                            pages_total += 1
                            if not results:
                                break

                            with connect() as conn:
                                for work in results:
                                    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
                                    title = work.get("title") or "Untitled"
                                    ids = work.get("ids") or {}
                                    source_url = ((work.get("primary_location") or {}).get("landing_page_url")
                                                  or work.get("doi") or work.get("id") or "")
                                    paper_id, was_added = upsert_paper(conn, {
                                        "openalex_id": short_id(work.get("id")), "arxiv_id": short_id(ids.get("arxiv")),
                                        "doi": work.get("doi"), "title": title, "abstract": abstract,
                                        "earliest_public_date": work.get("publication_date"),
                                        "formal_publication_date": work.get("publication_date"),
                                        "work_type": work.get("type"), "source_url": source_url, "raw": work,
                                    }, "openalex")
                                    added += int(was_added)
                                    for authorship in work.get("authorships") or []:
                                        author = authorship.get("author") or {}
                                        institutions = authorship.get("institutions") or []
                                        institution = institutions[0] if institutions else {}
                                        author_id = short_id(author.get("id"))
                                        conn.execute(
                                            """INSERT OR IGNORE INTO paper_authors(paper_id, author_id, display_name,
                                              institution_id, institution_name, disambiguation_status)
                                            VALUES(?, ?, ?, ?, ?, ?)""",
                                            (paper_id, author_id, author.get("display_name") or "Unknown",
                                             short_id(institution.get("id")), institution.get("display_name"),
                                             "identified" if author_id else "unresolved"),
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

                            count = len(results)
                            year_fetched += count
                            direction_fetched += count
                            fetched_total += count
                            cursor = meta.get("next_cursor")
                            if count < page_size or not cursor:
                                break
                        if year_fetched < year_matched:
                            year_complete = False
                        direction_complete = direction_complete and year_complete

                    coverage = direction_fetched / direction_matched if direction_matched else None
                    with connect() as conn:
                        conn.execute(
                            """INSERT OR REPLACE INTO research_queries(
                              batch_id, direction_slug, matched_total, fetched_count, classified_count,
                              relevant_count, is_complete, api_cost_usd, pages_fetched, coverage_ratio,
                              query_strategy, year_start, year_end, error)
                            VALUES(?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'yearly_cursor_title_abstract_v3', ?, ?, ?)""",
                            (batch_id, direction["slug"], direction_matched, direction_fetched, direction_fetched,
                             direction_relevant, int(direction_complete), direction_pages,
                             round(coverage, 6) if coverage is not None else None,
                             first_year, current_year, "\n".join(direction_failures) or None),
                        )
        finally:
            await client.close()

        with connect() as conn:
            complete_directions = conn.execute(
                "SELECT COUNT(*) FROM research_queries WHERE batch_id=? AND is_complete=1", (batch_id,)
            ).fetchone()[0]
            status = "complete" if complete_directions == len(directions) and not failures else "partial"
            conn.execute(
                """UPDATE scan_batches SET finished_at=?, status=?, reported_total=?, fetched_count=?,
                  relevant_count=?, error=?, raw_path=? WHERE id=?""",
                (utcnow(), status, reported_total, fetched_total, relevant_total,
                 "\n".join(failures)[:4000] or None, str(raw_path), batch_id),
            )
        self.progress("论文数据", 1, f"按年分页 {pages_total} 页 / {fetched_total} 条；{complete_directions}/{len(directions)} 个方向完整")
        return {"added": added, "fetched": fetched_total, "relevant": relevant_total,
                "failures": len(failures), "complete_directions": complete_directions,
                "directions": len(directions), "batch_id": batch_id}
