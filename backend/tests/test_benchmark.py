import json

from app.db import connect, init_db, utcnow
from app.services.benchmark import benchmark_summary, import_benchmark
from app.services.config_loader import sync_config
from app.services.factors import mine_jd_factors
from app.settings import settings


def test_benchmark_is_reference_not_open_job(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.db"))
    init_db()
    sync_config()
    source = tmp_path / "benchmark.jsonl"
    records = [
        {
            "company": "示例公司", "program": "顶尖计划", "id": 1,
            "title": "多模态大模型研究员", "location": "北京", "type": "校招",
            "description": "研究多模态大模型和 Agent", "requirement": "博士，顶会论文，熟悉 Python",
        },
        {
            "company": "示例公司", "program": "顶尖计划", "id": 2,
            "title": "AI Infra 工程师", "location": "上海", "type": "实习",
            "description": "训练框架和推理部署", "requirement": "熟悉 C++ 和 PyTorch",
        },
    ]
    source.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records), encoding="utf-8")

    report = import_benchmark(source)
    assert report["target_jobs"] == 2
    assert report["matched_jobs"] == 0
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE status='open'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE status='reference'").fetchone()[0] == 2


def test_exact_live_match_counts_toward_recall(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.db"))
    init_db()
    sync_config()
    source = tmp_path / "benchmark.jsonl"
    source.write_text(json.dumps({
        "company": "示例公司", "program": "顶尖计划", "id": "abc",
        "title": "Agent 算法工程师", "location": "杭州", "type": "校招",
        "description": "Agent 与强化学习", "requirement": "Python",
    }, ensure_ascii=False), encoding="utf-8")
    import_benchmark(source)
    now = utcnow()
    with connect() as conn:
        conn.execute(
            """INSERT INTO jobs(source_namespace,source_slug,company,market,external_id,title,url,
              first_seen_at,last_seen_at,content_hash,source_kind,status)
              VALUES('official','example','示例公司','cn_internet','abc','Agent 算法工程师',
              'https://example.com/job',?,?, 'hash','live','open')""",
            (now, now),
        )
    report = benchmark_summary()
    assert report["matched_jobs"] == 1
    assert report["job_recall"] == 1.0
    factors = mine_jd_factors()
    by_slug = {item["slug"]: item for item in factors["factors"]}
    assert by_slug["agents"]["jobs"] == 1
    assert by_slug["python_cpp"]["jobs"] == 1

