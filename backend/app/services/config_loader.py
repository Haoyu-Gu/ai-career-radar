from pathlib import Path
from typing import Any

import yaml

from ..db import connect
from ..settings import settings


def load_yaml(name: str) -> dict[str, Any]:
    path = settings.config_dir / name
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_sources() -> dict[str, Any]:
    return load_yaml("sources.yaml")


def load_directions() -> dict[str, Any]:
    return load_yaml("directions.yaml")


def sync_config() -> None:
    sources_doc = load_sources()
    directions_doc = load_directions()
    with connect() as conn:
        for item in sources_doc["sources"]:
            conn.execute(
                """
                INSERT INTO sources(slug, company, group_name, market, homepage, careers_url,
                                    adapter, namespace, token, status, notes)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                  company=excluded.company, group_name=excluded.group_name,
                  market=excluded.market, homepage=excluded.homepage,
                  careers_url=COALESCE(excluded.careers_url, sources.careers_url),
                  adapter=CASE
                    WHEN excluded.adapter='manual' AND sources.adapter IN ('greenhouse','lever') THEN sources.adapter
                    ELSE excluded.adapter END,
                  namespace=CASE
                    WHEN excluded.adapter='manual' AND sources.adapter IN ('greenhouse','lever') THEN sources.namespace
                    ELSE excluded.namespace END,
                  token=CASE
                    WHEN excluded.adapter='manual' AND sources.adapter IN ('greenhouse','lever') THEN sources.token
                    ELSE excluded.token END,
                  status=CASE
                    WHEN sources.status IN ('pending','unchecked','accessible')
                      AND excluded.status IN ('verified','accessible','blocked') THEN excluded.status
                    ELSE sources.status END,
                  notes=excluded.notes
                """,
                (
                    item["slug"], item["company"], item.get("group"), item["market"],
                    item.get("homepage"), item.get("careers_url"), item["adapter"],
                    item["namespace"], item.get("token"), item.get("status", "unchecked"),
                    item.get("notes"),
                ),
            )
        for item in directions_doc["directions"]:
            conn.execute(
                """
                INSERT INTO directions(slug, name_zh, dimension, description, query, config_version)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                  name_zh=excluded.name_zh, dimension=excluded.dimension,
                  description=excluded.description, query=excluded.query,
                  config_version=excluded.config_version
                """,
                (
                    item["slug"], item["name_zh"], item["dimension"],
                    item.get("description"), item.get("query"), directions_doc["version"],
                ),
            )


def project_path(*parts: str) -> Path:
    return Path(__file__).resolve().parents[3].joinpath(*parts)
