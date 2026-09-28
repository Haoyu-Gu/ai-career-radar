import argparse
import asyncio

from .collectors.arxiv import ArxivCollector
from .collectors.crossref import CrossrefCollector
from .collectors.jobs import JobCollector
from .collectors.openalex import OpenAlexCollector
from .db import init_db
from .settings import settings
from .services.config_loader import sync_config
from .services.benchmark import import_benchmark
from .services.metrics import snapshot_current_jobs
from .services.refresh import create_refresh, run_refresh


def progress(phase: str, value: float, message: str) -> None:
    print(f"[{value:>5.0%}] {phase}: {message}", flush=True)


async def main_async(command: str, benchmark_path: str | None = None) -> None:
    init_db()
    sync_config()
    if command == "init":
        print("数据库与配置已初始化")
    elif command == "refresh":
        run_id = create_refresh()
        await run_refresh(run_id)
        print(f"刷新完成：{run_id}")
    elif command == "jobs":
        await JobCollector(progress).collect_all()
        snapshot_current_jobs()
    elif command == "papers":
        if settings.openalex_api_key:
            await OpenAlexCollector(progress).collect()
        else:
            print("未配置 OPENALEX_API_KEY，跳过 OpenAlex，使用 Crossref 公共索引", flush=True)
        await CrossrefCollector(progress).collect()
        await ArxivCollector(progress).collect()
    elif command == "benchmark":
        if not benchmark_path:
            raise SystemExit("benchmark 命令需要 --path JSONL_FILE")
        report = import_benchmark(benchmark_path)
        print(
            f"基准已导入：{report['target_jobs']} 条 / {report['target_companies']} 家公司 / "
            f"{report['target_programs']} 个人才计划；当前官网抓取召回 "
            f"{report['matched_jobs']}/{report['target_jobs']} ({report['job_recall']:.1%})"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 就业雷达数据工具")
    parser.add_argument("command", choices=["init", "refresh", "jobs", "papers", "benchmark"])
    parser.add_argument("--path", help="同学提供的人才计划 JSONL 验收基准")
    args = parser.parse_args()
    asyncio.run(main_async(args.command, args.path))


if __name__ == "__main__":
    main()
