import asyncio
import json
import re
import uuid
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Callable

import httpx

from ..db import connect, utcnow
from ..services.classify import direction_matches
from ..services.config_loader import load_directions
from ..services.papers import upsert_paper
from ..settings import settings


Progress = Callable[[str, float, str], None]
DC = "{http://purl.org/dc/elements/1.1/}"


def _clean_html(value: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value or ""))).strip()


class ArxivCollector:
    """Official arXiv RSS incremental collector; avoids the legacy API's 406 failures."""

    def __init__(self, progress: Progress | None = None):
        self.progress = progress or (lambda *_: None)

    async def collect(self, max_results: int = 0) -> dict[str, int]:
        del max_results  # Kept for backwards-compatible callers.
        doc = load_directions()
        batch_id = f"arxiv-rss-{uuid.uuid4().hex[:12]}"
        with connect() as conn:
            conn.execute(
                "INSERT INTO scan_batches(id, kind, source_slug, started_at, status, config_version) VALUES(?, 'papers', 'arxiv', ?, 'running', ?)",
                (batch_id, utcnow(), doc["version"]),
            )

        categories = settings.arxiv_category_list
        semaphore = asyncio.Semaphore(3)

        async def fetch(category: str) -> tuple[str, str]:
            async with semaphore:
                async with httpx.AsyncClient(
                    timeout=settings.request_timeout_seconds,
                    follow_redirects=True,
                    headers={"User-Agent": "AITrendRadar/0.2 (personal research metadata client)", "Accept": "application/rss+xml"},
                ) as client:
                    response = await client.get(f"https://rss.arxiv.org/rss/{category}")
                    response.raise_for_status()
                    return category, response.text

        self.progress("近期预印本", 0.05, f"读取 {len(categories)} 个 arXiv 分类 RSS")
        results = await asyncio.gather(*(fetch(category) for category in categories), return_exceptions=True)
        failures: list[str] = []
        records: dict[str, dict] = {}
        raw_feeds: dict[str, str] = {}
        for result in results:
            if isinstance(result, Exception):
                failures.append(str(result)[:500])
                continue
            category, xml_text = result
            raw_feeds[category] = xml_text
            root = ET.fromstring(xml_text)
            for item in root.findall("./channel/item"):
                link = (item.findtext("link") or "").strip()
                arxiv_id = link.rstrip("/").rsplit("/", 1)[-1].split("v", 1)[0]
                if not arxiv_id:
                    continue
                published_raw = item.findtext("pubDate") or item.findtext(f"{DC}date")
                try:
                    published = parsedate_to_datetime(published_raw).date().isoformat() if published_raw else None
                except (TypeError, ValueError):
                    published = (published_raw or "")[:10] or None
                creator = item.findtext(f"{DC}creator") or ""
                records[arxiv_id] = {
                    "arxiv_id": arxiv_id,
                    "title": _clean_html(item.findtext("title")) or "Untitled",
                    "abstract": _clean_html(item.findtext("description")),
                    "earliest_public_date": published,
                    "work_type": "preprint",
                    "source_url": link,
                    "authors": [name.strip() for name in re.split(r",|;|\band\b", creator) if name.strip()],
                    "categories": sorted(set((records.get(arxiv_id) or {}).get("categories", [])) | {category}),
                }

        settings.raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = settings.raw_dir / f"{batch_id}.json"
        raw_path.write_text(json.dumps({"feeds": raw_feeds, "records": list(records.values())}, ensure_ascii=False), encoding="utf-8")

        added = relevant = 0
        with connect() as conn:
            for record in records.values():
                paper_id, was_added = upsert_paper(conn, {**record, "raw": record}, "arxiv-rss")
                added += int(was_added)
                for author_name in record["authors"]:
                    conn.execute(
                        """INSERT OR IGNORE INTO paper_authors(paper_id, author_id, display_name, disambiguation_status)
                        VALUES(?, NULL, ?, 'unresolved')""",
                        (paper_id, author_name),
                    )
                matches = direction_matches(f"{record['title']}\n{record['abstract']}")
                relevant += int(bool(matches))
                for match in matches:
                    conn.execute(
                        """INSERT OR REPLACE INTO paper_directions(paper_id, direction_slug, relevance,
                          confidence, rationale, classifier, version) VALUES(?, ?, ?, ?, ?, 'rules', ?)""",
                        (paper_id, match["slug"], match["relevance"], match["confidence"],
                         match["rationale"], doc["version"]),
                    )
            status = "complete" if not failures else "partial"
            conn.execute(
                """UPDATE scan_batches SET finished_at=?, status=?, reported_total=?, fetched_count=?,
                  relevant_count=?, error=?, raw_path=? WHERE id=?""",
                (utcnow(), status, len(records), len(records), relevant,
                 "\n".join(failures)[:4000] or None, str(raw_path), batch_id),
            )
        self.progress("近期预印本", 1, f"RSS 增量 {len(records)} 篇，规则相关 {relevant} 篇")
        return {"added": added, "fetched": len(records), "relevant": relevant, "failures": len(failures)}
