import asyncio
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import httpx

from ..db import connect, json_dumps, rows, utcnow
from ..services.classify import (
    job_direction_matches,
    infer_education,
    infer_experience,
    infer_recruitment_type,
    infer_work_nature,
    is_ai_job,
)
from ..services.config_loader import load_directions, load_sources
from ..services.salary import find_salary_snippet, parse_salary
from ..services.text import plain_text
from ..settings import settings
from .http import CachedHttpClient


Progress = Callable[[str, float, str], None]


def _content_hash(*values: Any) -> str:
    return hashlib.sha256("\n".join(str(v or "") for v in values).encode()).hexdigest()


def _normalize_greenhouse(source: dict, raw: dict) -> dict:
    body = plain_text(raw.get("content"))
    departments = raw.get("departments") or []
    offices = raw.get("offices") or []
    location = (raw.get("location") or {}).get("name") or "; ".join(
        item.get("name", "") for item in offices
    )
    return {
        "external_id": str(raw.get("id")),
        "title": (raw.get("title") or "Untitled").strip(),
        "department": "; ".join(item.get("name", "") for item in departments),
        "location": location,
        "body": body,
        "benefits_raw": "",
        "salary_raw": find_salary_snippet(body),
        "url": raw.get("absolute_url") or source.get("careers_url"),
        "published_at": raw.get("first_published") or raw.get("updated_at"),
        "raw": raw,
    }


def _normalize_lever(source: dict, raw: dict) -> dict:
    categories = raw.get("categories") or {}
    lists = raw.get("lists") or []
    list_text = "\n".join(f"{item.get('text', '')}: {plain_text(item.get('content'))}" for item in lists)
    body = "\n".join(
        part for part in [raw.get("openingPlain"), raw.get("descriptionBodyPlain"), list_text, raw.get("additionalPlain")] if part
    )
    salary_range = raw.get("salaryRange") or {}
    salary_raw = raw.get("salaryDescriptionPlain")
    if salary_range:
        salary_raw = salary_raw or (
            f"{salary_range.get('currency', '')} {salary_range.get('min', '')}-"
            f"{salary_range.get('max', '')} {salary_range.get('interval', '')}"
        )
    return {
        "external_id": str(raw.get("id")),
        "title": (raw.get("text") or "Untitled").strip(),
        "department": categories.get("department") or categories.get("team"),
        "location": categories.get("location") or "; ".join(categories.get("allLocations") or []),
        "body": plain_text(body),
        "benefits_raw": plain_text(raw.get("additionalPlain")),
        "salary_raw": salary_raw,
        "url": raw.get("hostedUrl") or source.get("careers_url"),
        "published_at": None,
        "raw": raw,
        "salary_range": salary_range,
        "commitment": categories.get("commitment"),
    }


def _normalize_baidu(source: dict, raw: dict) -> dict:
    responsibilities = plain_text(raw.get("workContent"))
    requirements = plain_text(raw.get("serviceCondition"))
    body = "\n".join(part for part in [responsibilities, requirements] if part)
    return {
        "external_id": raw.get("postId") or raw.get("jobId"),
        "title": (raw.get("name") or "Untitled").strip(),
        "department": raw.get("projectType") or raw.get("orgName"),
        "location": raw.get("workPlace"),
        "body": body,
        "responsibilities": responsibilities,
        "requirements": requirements,
        "benefits_raw": "",
        "salary_raw": None,
        "url": source["careers_url"],
        "published_at": raw.get("publishDate"),
        "raw": raw,
        "recruitment_type_override": "campus",
        "headcount": int(raw["recruitNum"]) if str(raw.get("recruitNum", "")).isdigit() and int(raw["recruitNum"]) > 0 else None,
        "talent_program": "AIDU",
        "job_category": plain_text(raw.get("postType")),
        "business_group": plain_text(raw.get("orgName")),
    }


def _normalize_kuaishou(source: dict, raw: dict) -> dict:
    responsibilities = plain_text(raw.get("description"))
    requirements = plain_text(raw.get("positionDemand"))
    body = "\n".join(part for part in (responsibilities, requirements) if part)
    locations = ";".join(
        str(item.get("name") or "").strip() for item in (raw.get("workLocationDicts") or [])
        if item.get("name")
    )
    subproject = str(raw.get("recruitSubProjectCode") or "")
    code = str(raw.get("code") or "")
    external_id = str(raw.get("id") or code)
    detail_url = (
        f"https://campus.kuaishou.cn/recruit/campus/e/#/campus/job-info/{external_id}"
        f"?code={code}&recruitSubProjectCodes={subproject}"
    )
    nature = str(raw.get("positionNatureCode") or "").lower()
    return {
        "external_id": external_id,
        "title": str(raw.get("name") or "未命名岗位").strip(),
        "department": raw.get("departmentName") or raw.get("positionCategoryCode"),
        "location": locations,
        "body": body,
        "responsibilities": responsibilities,
        "requirements": requirements,
        "benefits_raw": "",
        "salary_raw": None,
        "url": detail_url,
        "published_at": raw.get("releaseTime"),
        "raw": raw,
        "recruitment_type_override": "internship" if nature == "intern" else "campus",
        "headcount": int(raw["recruitNumber"]) if str(raw.get("recruitNumber", "")).isdigit() and int(raw["recruitNumber"]) > 0 else None,
        "talent_program": "快Star",
        "job_category": plain_text(raw.get("positionCategoryCode")),
        "business_group": plain_text(raw.get("departmentName")),
    }


def _nested_name(value: Any) -> str:
    if isinstance(value, dict):
        nested = value.get("name")
        if isinstance(nested, dict):
            return str(nested.get("zh_cn") or nested.get("i18n") or nested.get("en_us") or "").strip()
        return str(nested or value.get("i18n_name") or value.get("en_name") or "").strip()
    return str(value or "").strip()


def _normalize_bytedance(source: dict, raw: dict) -> dict:
    responsibilities = plain_text(raw.get("description"))
    requirements = plain_text(raw.get("requirement"))
    body = "\n".join(part for part in (responsibilities, requirements) if part)
    city_values = raw.get("city_list") or ([raw.get("city_info")] if raw.get("city_info") else [])
    locations = ";".join(filter(None, (_nested_name(item) for item in city_values)))
    recruit_type = _nested_name(raw.get("recruit_type"))
    timestamp = raw.get("publish_time")
    published_at = None
    if isinstance(timestamp, (int, float)):
        published_at = datetime.fromtimestamp(timestamp / 1000, UTC).isoformat(timespec="seconds")
    job_info = raw.get("job_post_info") or {}
    headcount = job_info.get("head_count")
    return {
        "external_id": str(raw.get("id")),
        "title": str(raw.get("title") or "未命名岗位").strip(),
        "department": _nested_name(raw.get("job_function")) or _nested_name(raw.get("job_category")),
        "location": locations,
        "body": body,
        "responsibilities": responsibilities,
        "requirements": requirements,
        "benefits_raw": "",
        "salary_raw": None,
        "url": f"https://jobs.bytedance.com/campus/position/detail/{raw.get('id')}",
        "published_at": published_at,
        "raw": raw,
        "recruitment_type_override": "internship" if "实习" in recruit_type else "campus",
        "headcount": int(headcount) if str(headcount or "").isdigit() and int(headcount) > 0 else None,
        "talent_program": "前沿技术领域人才",
        "job_category": _nested_name(raw.get("job_category")),
        "business_group": _nested_name(raw.get("job_function")),
    }


def _normalize_tencent(source: dict, raw: dict) -> dict:
    detail = raw.get("detail") or {}
    responsibilities = plain_text(
        detail.get("topicDetail") or detail.get("desc") or raw.get("description")
    )
    requirements = plain_text(
        detail.get("topicRequirement") or detail.get("request") or raw.get("requirement")
    )
    body = "\n".join(part for part in (responsibilities, requirements) if part)
    locations = detail.get("workCityList") or []
    location = ";".join(str(item).strip() for item in locations if str(item).strip())
    if not location:
        location = ";".join(str(raw.get("workCities") or "").split())
    post_id = str(raw.get("postId") or detail.get("postId") or raw.get("id"))
    project_name = str(detail.get("projectName") or raw.get("projectName") or "青云计划")
    business_groups = detail.get("intentionBGDList") or []
    business_group = ";".join(
        str(item.get("showTitle") or item.get("title") or "").strip()
        for item in business_groups if item.get("showTitle") or item.get("title")
    ) or plain_text(raw.get("bgs"))
    return {
        "external_id": post_id,
        "title": str(detail.get("title") or raw.get("positionTitle") or "未命名岗位").strip(),
        "department": business_group,
        "location": location,
        "body": body,
        "responsibilities": responsibilities,
        "requirements": requirements,
        "benefits_raw": "",
        "salary_raw": None,
        "url": f"https://join.qq.com/jobdesc.html?postId={post_id}",
        "published_at": None,
        "raw": raw,
        "recruitment_type_override": "internship" if "实习" in project_name else "campus",
        "headcount": None,
        "talent_program": "青云计划",
        "job_category": plain_text(detail.get("techTagName")) or str(raw.get("positionFamily") or ""),
        "business_group": business_group,
    }


def _normalize_alibaba(source: dict, raw: dict) -> dict:
    responsibilities = plain_text(raw.get("description"))
    requirements = plain_text(raw.get("requirement"))
    body = "\n".join(part for part in (responsibilities, requirements) if part)
    locations = ";".join(str(item).strip() for item in (raw.get("workLocations") or []) if str(item).strip())
    circles = raw.get("circleNames") or []
    business_group = ";".join(str(item).strip() for item in circles if str(item).strip())
    category_type = str(raw.get("categoryType") or "").lower()
    position_id = str(raw.get("id"))
    timestamp = raw.get("modifyTime") or raw.get("publishTime")
    published_at = None
    if isinstance(timestamp, (int, float)):
        published_at = datetime.fromtimestamp(timestamp / 1000, UTC).isoformat(timespec="seconds")
    return {
        "external_id": position_id,
        "title": str(raw.get("name") or "未命名岗位").strip(),
        "department": plain_text(raw.get("department")) or business_group,
        "location": locations,
        "body": body,
        "responsibilities": responsibilities,
        "requirements": requirements,
        "benefits_raw": "",
        "salary_raw": None,
        "url": f"https://campus-talent.alibaba.com/campus/position?batchId={raw.get('batchId') or '100000760001'}",
        "published_at": published_at,
        "raw": raw,
        "recruitment_type_override": "internship" if "intern" in category_type else "campus",
        "headcount": None,
        "talent_program": "阿里星（公司级技术岗位池）",
        "job_category": plain_text(raw.get("categoryName")),
        "business_group": business_group,
    }


def _normalize_meituan(source: dict, raw: dict) -> dict:
    responsibilities = plain_text(raw.get("jobDuty"))
    requirements = plain_text(raw.get("jobRequirement"))
    body = "\n".join(part for part in (responsibilities, requirements) if part)
    locations = ";".join(
        str(item.get("name") or "").strip() for item in (raw.get("cityList") or []) if item.get("name")
    )
    timestamp = raw.get("refreshTime") or raw.get("firstPostTime")
    published_at = None
    if isinstance(timestamp, (int, float)):
        published_at = datetime.fromtimestamp(timestamp / 1000, UTC).isoformat(timespec="seconds")
    external_id = str(raw.get("jobUnionId"))
    return {
        "external_id": external_id,
        "title": str(raw.get("name") or "未命名岗位").strip(),
        "department": plain_text(raw.get("jobFamilyGroup")),
        "location": locations,
        "body": body,
        "responsibilities": responsibilities,
        "requirements": requirements,
        "benefits_raw": plain_text(raw.get("otherInfo")),
        "salary_raw": None,
        "url": f"https://career.meituan.com/web/position/detail?jobUnionId={external_id}",
        "published_at": published_at,
        "raw": raw,
        "recruitment_type_override": "internship",
        "headcount": None,
        "talent_program": "北斗计划",
        "job_category": plain_text(raw.get("jobFamily")),
        "business_group": plain_text(raw.get("jobFamilyGroup")),
    }


def _normalize_ant(source: dict, raw: dict) -> dict:
    responsibilities = plain_text(raw.get("description"))
    requirements = plain_text(raw.get("requirement"))
    body = "\n".join(part for part in (responsibilities, requirements) if part)
    locations = ";".join(str(item).strip() for item in (raw.get("workLocations") or []) if str(item).strip())
    batch_name = str(raw.get("batchName") or "蚂蚁人才计划")
    department_path = raw.get("departmentPath") or []
    if isinstance(department_path, list):
        business_group = ";".join(str(item).strip() for item in department_path if str(item).strip())
    else:
        business_group = plain_text(department_path)
    external_id = str(raw.get("id"))
    return {
        "external_id": external_id,
        "title": str(raw.get("name") or "未命名岗位").strip(),
        "department": plain_text(raw.get("department")) or business_group,
        "location": locations,
        "body": body,
        "responsibilities": responsibilities,
        "requirements": requirements,
        "benefits_raw": "",
        "salary_raw": None,
        "url": f"https://talent.antgroup.com/campus-position?id={external_id}",
        "published_at": raw.get("publishTime"),
        "raw": raw,
        "recruitment_type_override": "internship" if "实习" in batch_name else "campus",
        "headcount": None,
        "talent_program": batch_name,
        "job_category": plain_text(raw.get("categoryName")),
        "business_group": business_group,
    }


class JobCollector:
    def __init__(self, progress: Progress | None = None):
        self.progress = progress or (lambda *_: None)

    async def discover_public_ats(self) -> int:
        """Find documented Greenhouse/Lever boards linked from known official career pages."""
        with connect() as conn:
            candidates = rows(
                conn,
                """
                SELECT slug, company, careers_url FROM sources
                WHERE adapter='manual' AND careers_url IS NOT NULL
                ORDER BY company
                """,
            )
        patterns = [
            ("greenhouse", "greenhouse", re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([A-Za-z0-9_-]+)", re.I)),
            ("lever", "lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)", re.I)),
        ]
        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "AITrendRadar/0.2 (personal research dashboard)"},
        ) as http:
            discovery_semaphore = asyncio.Semaphore(5)

            async def check_candidate(index: int, source: dict[str, Any]) -> int:
                async with discovery_semaphore:
                    return await inspect_candidate(index, source)

            async def inspect_candidate(index: int, source: dict[str, Any]) -> int:
                self.progress(
                    "寻找招聘入口",
                    index / max(len(candidates), 1),
                    f"正在检查 {source['company']} 的公开招聘页",
                )
                try:
                    response = await http.get(source["careers_url"])
                    response.raise_for_status()
                    searchable = response.url.__str__() + "\n" + response.text
                    discovered = None
                    for adapter, namespace, pattern in patterns:
                        match = pattern.search(searchable)
                        if match:
                            discovered = (adapter, namespace, match.group(1))
                            break
                    with connect() as conn:
                        if discovered:
                            adapter, namespace, token = discovered
                            conn.execute(
                                """
                                UPDATE sources SET adapter=?, namespace=?, token=?, status='accessible',
                                  last_verified_at=?, last_error=NULL,
                                  notes=COALESCE(notes || '；', '') || '自动从官网招聘页发现公开 ATS 入口'
                                WHERE slug=?
                                """,
                                (adapter, namespace, token, utcnow(), source["slug"]),
                            )
                            return 1
                        else:
                            conn.execute(
                                "UPDATE sources SET last_verified_at=? WHERE slug=?",
                                (utcnow(), source["slug"]),
                            )
                except Exception as exc:
                    with connect() as conn:
                        conn.execute(
                            "UPDATE sources SET last_verified_at=?, last_error=? WHERE slug=?",
                            (utcnow(), f"官网入口检查失败：{str(exc)[:300]}", source["slug"]),
                        )
                return 0

            found = sum(await asyncio.gather(*(
                check_candidate(index, source) for index, source in enumerate(candidates)
            )))
        return found

    async def collect_all(self) -> dict[str, int]:
        sources_doc = load_sources()
        await self.discover_public_ats()
        with connect() as conn:
            sources = rows(
                conn,
                """
                SELECT slug, company, group_name, market, homepage, careers_url, adapter,
                  namespace, token, status, notes FROM sources
                WHERE adapter IN ('greenhouse','lever','baidu_ssr','kuaishou_campus','bytedance_campus','tencent_qingyun','alibaba_campus','meituan_beidou','ant_talent') ORDER BY company
                """,
            )
        for source in sources:
            source["group"] = source.get("group_name")
        # Independent company APIs are I/O-bound. Three workers keeps the refresh
        # quick without creating an aggressive burst against any single host.
        source_semaphore = asyncio.Semaphore(3)

        async def collect_one(index: int, source: dict[str, Any]) -> dict[str, Any]:
            async with source_semaphore:
                self.progress("招聘数据", index / max(len(sources), 1), f"正在检查 {source['company']}")
                return await self.collect_source(source, sources_doc["version"])

        results = await asyncio.gather(*(collect_one(index, source) for index, source in enumerate(sources)))
        added = sum(result["added"] for result in results)
        relevant = sum(result["relevant"] for result in results)
        complete = sum(int(result["status"] == "complete") for result in results)
        self.progress("招聘数据", 1, f"完成 {complete}/{len(sources)} 个公开 ATS 来源")
        return {"added": added, "relevant": relevant, "complete_sources": complete}

    async def collect_source(self, source: dict, config_version: str) -> dict[str, Any]:
        batch_id = f"jobs-{source['slug']}-{uuid.uuid4().hex[:12]}"
        now = utcnow()
        with connect() as conn:
            prior = conn.execute(
                "SELECT COUNT(*) FROM scan_batches WHERE source_slug=? AND kind='jobs' AND status='complete'",
                (source["slug"],),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO scan_batches(id, kind, source_slug, started_at, status, is_baseline, config_version) VALUES(?, 'jobs', ?, ?, 'running', ?, ?)",
                (batch_id, source["slug"], now, int(prior == 0), config_version),
            )

        client = CachedHttpClient(f"jobs-{source['slug']}", min_interval=0.5)
        status = "failed"
        error = None
        raw_payload: Any = None
        reported_total = 0
        normalized: list[dict] = []
        try:
            if source["adapter"] == "greenhouse":
                url = f"https://boards-api.greenhouse.io/v1/boards/{source['token']}/jobs"
                raw_payload, _, _ = await client.get_json(url, {"content": "true"}, cache_ttl_seconds=600)
                records = raw_payload.get("jobs", [])
                reported_total = int((raw_payload.get("meta") or {}).get("total") or len(records))
                normalized = [_normalize_greenhouse(source, item) for item in records]
                status = "complete" if len(records) == reported_total else "partial"
            elif source["adapter"] == "lever":
                url = f"https://api.lever.co/v0/postings/{source['token']}"
                skip = 0
                records = []
                while skip < settings.max_jobs_per_source:
                    page, _, _ = await client.get_json(
                        url, {"mode": "json", "skip": skip, "limit": 100}, cache_ttl_seconds=600
                    )
                    records.extend(page)
                    if len(page) < 100:
                        break
                    skip += len(page)
                reported_total = len(records)
                normalized = [_normalize_lever(source, item) for item in records]
                status = "partial" if skip >= settings.max_jobs_per_source else "complete"
            elif source["adapter"] == "kuaishou_campus":
                url = "https://campus.kuaishou.cn/recruit/campus/e/api/v1/open/positions/simple"
                page_num = 1
                page_size = 100
                records = []
                pages: list[dict[str, Any]] = []
                while len(records) < settings.max_jobs_per_source:
                    payload, _, _ = await client.post_json(
                        url,
                        {"pageNum": page_num, "pageSize": page_size, "positionLabel": "kstar"},
                        cache_ttl_seconds=600,
                        headers={
                            "Origin": "https://campus.kuaishou.cn",
                            "Referer": "https://campus.kuaishou.cn/recruit/campus/e/",
                        },
                    )
                    if payload.get("code") != 0:
                        raise RuntimeError(f"快手职位接口返回 code={payload.get('code')}: {payload.get('message')}")
                    result = payload.get("result") or {}
                    page_records = result.get("list") or []
                    reported_total = int(result.get("total") or 0)
                    pages.append(payload)
                    records.extend(page_records)
                    if not page_records or len(records) >= reported_total or result.get("isLastPage"):
                        break
                    page_num += 1
                raw_payload = {"endpoint": url, "pages": pages}
                normalized = [_normalize_kuaishou(source, item) for item in records]
                status = "complete" if reported_total == len(records) else "partial"
            elif source["adapter"] == "bytedance_campus":
                url = "https://jobs.bytedance.com/api/v1/search/job/posts"
                subject_ids = ["7624064258157889845", "7624086888207862069"]
                request_headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
                    "Origin": "https://jobs.bytedance.com",
                    "Referer": "https://jobs.bytedance.com/campus/position",
                    "website-path": "campus",
                    "Portal-Channel": "campus",
                    "Portal-Platform": "pc",
                    "accept-language": "zh-CN",
                }
                offset = 0
                page_size = 100
                records = []
                pages = []
                while len(records) < settings.max_jobs_per_source:
                    payload, _, _ = await client.post_json(
                        url,
                        {
                            "keyword": "", "limit": page_size, "offset": offset,
                            "job_hot_flag": None, "job_category_id_list": [], "tag_id_list": [],
                            "location_code_list": [], "subject_id_list": subject_ids,
                            "recruitment_id_list": [], "portal_type": 3,
                            "job_function_id_list": [], "storefront_id_list": [],
                            "job_post_id_list": [],
                        },
                        cache_ttl_seconds=600,
                        headers=request_headers,
                    )
                    if payload.get("code") != 0:
                        raise RuntimeError(f"字节职位接口返回 code={payload.get('code')}: {payload.get('message')}")
                    data = payload.get("data") or {}
                    page_records = data.get("job_post_list") or []
                    reported_total = int(data.get("count") or 0)
                    pages.append(payload)
                    records.extend(page_records)
                    if not page_records or len(records) >= reported_total:
                        break
                    offset += len(page_records)
                raw_payload = {"endpoint": url, "subject_ids": subject_ids, "pages": pages}
                normalized = [_normalize_bytedance(source, item) for item in records]
                status = "complete" if reported_total == len(records) else "partial"
            elif source["adapter"] == "tencent_qingyun":
                list_url = "https://join.qq.com/api/v1/position/searchPosition"
                request_headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
                    "Origin": "https://join.qq.com",
                    "Referer": "https://join.qq.com/post.html",
                }
                payload, _, _ = await client.post_json(
                    list_url,
                    {
                        "projectIdList": [], "projectMappingIdList": [14, 20],
                        "keyword": "", "bgList": [], "workCountryType": 0,
                        "workCityList": [], "recruitCityList": [], "positionFidList": [],
                        "pageIndex": 1, "pageSize": 1000,
                    },
                    cache_ttl_seconds=600,
                    headers=request_headers,
                )
                if payload.get("status") != 0:
                    raise RuntimeError(f"腾讯职位接口返回 status={payload.get('status')}: {payload.get('message')}")
                data = payload.get("data") or {}
                records = data.get("positionList") or []
                reported_total = int(data.get("count") or 0)

                # Details are independent public GETs. A small bounded pool cuts a 5-minute
                # serial crawl to seconds while keeping retries, cache and server load bounded.
                semaphore = asyncio.Semaphore(8)

                async def attach_detail(record: dict[str, Any]) -> dict[str, Any]:
                    post_id = str(record.get("postId") or "")
                    if not post_id:
                        return record
                    async with semaphore:
                        detail_payload, _, _ = await client.get_json(
                            "https://join.qq.com/api/v1/jobDetails/getJobDetailsByPostId",
                            {"postId": post_id},
                            cache_ttl_seconds=86400,
                        )
                    enriched = dict(record)
                    if detail_payload.get("status") == 0:
                        enriched["detail"] = detail_payload.get("data") or {}
                    return enriched

                records = list(await asyncio.gather(*(attach_detail(item) for item in records)))
                raw_payload = {"endpoint": list_url, "project_mapping_ids": [14, 20], "list": payload, "records": records}
                normalized = [_normalize_tencent(source, item) for item in records]
                status = "complete" if reported_total == len(records) else "partial"
            elif source["adapter"] == "alibaba_campus":
                page_url = "https://campus-talent.alibaba.com/campus/position?batchId=100000760001"
                browser_headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/plain, */*",
                }
                page_response = await client.client.get(page_url, headers=browser_headers)
                page_response.raise_for_status()
                token_match = re.search(r'__token__\s*:\s*"([^"]+)"', page_response.text)
                if not token_match:
                    raise RuntimeError("阿里校招页面未返回 CSRF token")
                csrf = token_match.group(1)
                api_url = f"https://campus-talent.alibaba.com/position/search?_csrf={csrf}"
                payload, _, _ = await client.post_json(
                    api_url,
                    {
                        "batchId": "100000760001", "pageIndex": 1, "pageSize": 1000,
                        "channel": "campus", "language": "zh",
                    },
                    cache_ttl_seconds=600,
                    headers={
                        **browser_headers,
                        "Origin": "https://campus-talent.alibaba.com",
                        "Referer": page_url,
                        "Content-Type": "application/json;charset=UTF-8",
                    },
                )
                if not payload.get("success"):
                    raise RuntimeError(f"阿里职位接口返回 error={payload.get('errorCode')}: {payload.get('errorMsg')}")
                content = payload.get("content") or {}
                records = content.get("datas") or []
                reported_total = int(content.get("totalCount") or 0)
                raw_payload = {"endpoint": api_url, "batch_id": "100000760001", "response": payload}
                normalized = [_normalize_alibaba(source, item) for item in records]
                status = "complete" if reported_total == len(records) else "partial"
            elif source["adapter"] == "meituan_beidou":
                api_url = "https://zhaopin.meituan.com/api/official/job/getJobList"
                request_headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
                    "Origin": "https://career.meituan.com",
                    "Referer": "https://career.meituan.com/web/beidou?hiringType=2_2-3",
                }
                page_no = 1
                page_size = 100
                records = []
                pages = []
                while len(records) < settings.max_jobs_per_source:
                    payload, _, _ = await client.post_json(
                        api_url,
                        {
                            "page": {"pageNo": page_no, "pageSize": page_size},
                            "jobShareType": "1", "keywords": "", "cityList": [],
                            "department": [], "jfJgList": [],
                            "jobType": [{"code": "2", "subCode": ["2-3"]}],
                            "typeCode": ["2-3"], "specialCode": [],
                        },
                        cache_ttl_seconds=600,
                        headers=request_headers,
                    )
                    if payload.get("status") != 1:
                        raise RuntimeError(f"美团职位接口返回 status={payload.get('status')}: {payload.get('message')}")
                    data = payload.get("data") or {}
                    page_records = data.get("list") or []
                    page_info = data.get("page") or {}
                    reported_total = int(page_info.get("totalCount") or 0)
                    pages.append(payload)
                    records.extend(page_records)
                    if not page_records or page_no >= int(page_info.get("totalPage") or 1):
                        break
                    page_no += 1
                raw_payload = {"endpoint": api_url, "type_code": "2-3", "pages": pages}
                normalized = [_normalize_meituan(source, item) for item in records]
                status = "complete" if reported_total == len(records) else "partial"
            elif source["adapter"] == "ant_talent":
                # The public site proxies /api to this production service. Calling the
                # documented target directly avoids the outer site's method-only gateway.
                api_url = "https://hrcareersweb.antgroup.com/api/campus/position/search"
                request_headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
                    "Origin": "https://talent.antgroup.com",
                    "Referer": "https://talent.antgroup.com/campus-full-list?type=talentPlan",
                    "Front-User-Id": str(uuid.uuid4().int % 10**18).zfill(18),
                }
                first_payload, _, _ = await client.post_json(
                    api_url,
                    {
                        "regions": "", "subCategories": "", "bgCode": "", "key": "",
                        "pageIndex": 1, "pageSize": 10, "recruitType": [],
                        "batchIds": [], "isStar": None,
                    },
                    cache_ttl_seconds=600,
                    headers=request_headers,
                )
                if not first_payload.get("success"):
                    raise RuntimeError(f"蚂蚁职位接口返回 error={first_payload.get('errorCode')}: {first_payload.get('errorMsg')}")
                raw_total = int(first_payload.get("totalCount") or 0)
                total_pages = max(1, (raw_total + 9) // 10)
                ant_semaphore = asyncio.Semaphore(8)

                async def fetch_ant_page(page_no: int) -> dict[str, Any]:
                    async with ant_semaphore:
                        page_payload, _, _ = await client.post_json(
                            api_url,
                            {
                                "regions": "", "subCategories": "", "bgCode": "", "key": "",
                                "pageIndex": page_no, "pageSize": 10, "recruitType": [],
                                "batchIds": [], "isStar": None,
                            },
                            cache_ttl_seconds=600,
                            headers=request_headers,
                        )
                    return page_payload

                remaining = await asyncio.gather(*(fetch_ant_page(page) for page in range(2, total_pages + 1)))
                pages = [first_payload, *remaining]
                all_records = [item for page in pages for item in (page.get("content") or [])]
                records = [
                    item for item in all_records
                    if "Plan A" in str(item.get("batchName") or "") or "研究型实习" in str(item.get("batchName") or "")
                ]
                reported_total = len(records)
                raw_payload = {
                    "endpoint": api_url, "site_reported_total": raw_total,
                    "talent_program_total": reported_total, "pages": pages,
                }
                normalized = [_normalize_ant(source, item) for item in records]
                status = "complete" if len(all_records) == raw_total else "partial"
            else:
                async with httpx.AsyncClient(
                    timeout=settings.request_timeout_seconds,
                    follow_redirects=True,
                    headers={"User-Agent": "AITrendRadar/0.1 (personal research dashboard)"},
                ) as http:
                    response = await http.get(source["careers_url"])
                    response.raise_for_status()
                match = re.search(
                    r"window\.__INITIAL_DATA__\s*=(\{.*?\});\s*window\.prefix",
                    response.text,
                    re.DOTALL,
                )
                if not match:
                    raise RuntimeError("Baidu SSR initial data was not found")
                serialized = re.sub(r":undefined([,}])", r":null\1", match.group(1))
                initial_data = json.loads(serialized)
                list_data = initial_data.get("listData") or {}
                records = list_data.get("listDetailData") or []
                reported_total = int(list_data.get("total") or len(records))
                raw_payload = {"url": source["careers_url"], "initial_data": initial_data}
                normalized = [_normalize_baidu(source, item) for item in records]
                status = "complete" if len(records) == reported_total else "partial"
        except Exception as exc:
            error = str(exc)[:800]
            status = "failed"
        finally:
            await client.close()

        raw_path = settings.raw_dir / f"{batch_id}.json"
        if raw_payload is not None:
            raw_path.write_text(json.dumps(raw_payload, ensure_ascii=False), encoding="utf-8")

        added = 0
        relevant = 0
        observed_ids: list[int] = []
        with connect() as conn:
            previous_complete = conn.execute(
                """
                SELECT fetched_count FROM scan_batches
                WHERE source_slug=? AND kind='jobs' AND status='complete' AND id<>?
                ORDER BY finished_at DESC, rowid DESC LIMIT 1
                """,
                (source["slug"], batch_id),
            ).fetchone()
            previous_count = int(previous_complete[0]) if previous_complete else 0
            if (
                status == "complete" and previous_count >= 20
                and len(normalized) < previous_count * settings.job_drop_alert_ratio
            ):
                status = "partial"
                error = (
                    f"异常下降保护：本次 {len(normalized)} 条，最近完整扫描 {previous_count} 条；"
                    "未自动关闭旧岗位，等待下次复核"
                )
            conn.execute("DELETE FROM salaries WHERE lower_value <= 0 OR upper_value <= 0")
            for item in normalized:
                if not is_ai_job(item["title"], item["body"]):
                    continue
                relevant += 1
                full_text = f"{item['title']}\n{item['body']}"
                hash_value = _content_hash(item["title"], item["department"], item["location"], item["body"])
                existing = conn.execute(
                    "SELECT id FROM jobs WHERE source_namespace=? AND company=? AND external_id=?",
                    (source["namespace"], source["company"], item["external_id"]),
                ).fetchone()
                if existing:
                    job_id = existing[0]
                    conn.execute(
                        """
                        UPDATE jobs SET title=?, department=?, location=?, responsibilities=?, requirements=?,
                          benefits_raw=?, salary_raw=?, url=?, published_at=COALESCE(?, published_at),
                          last_seen_at=?, status='open', content_hash=?, raw_json=?, recruitment_type=?,
                          work_nature=?, education=?, experience=?, headcount=?, source_kind='live',
                          talent_program=?, job_category=?, business_group=? WHERE id=?
                        """,
                        (
                            item["title"], item["department"], item["location"],
                            item.get("responsibilities", item["body"]), item.get("requirements", item["body"]),
                            item["benefits_raw"], item["salary_raw"], item["url"], item["published_at"], now,
                            hash_value, json_dumps(item["raw"]), item.get("recruitment_type_override") or infer_recruitment_type(full_text),
                            infer_work_nature(item["title"], item["body"]), infer_education(full_text),
                            infer_experience(full_text), item.get("headcount"), item.get("talent_program"),
                            item.get("job_category"), item.get("business_group"), job_id,
                        ),
                    )
                else:
                    cursor = conn.execute(
                        """
                        INSERT INTO jobs(source_namespace, source_slug, company, company_group, market,
                          external_id, title, department, location, recruitment_type, work_nature,
                          education, experience, responsibilities, requirements, benefits_raw, salary_raw,
                          url, published_at, first_seen_at, last_seen_at, content_hash, raw_json, headcount,
                          source_kind, talent_program, job_category, business_group)
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'live', ?, ?, ?)
                        """,
                        (
                            source["namespace"], source["slug"], source["company"], source.get("group"),
                            source["market"], item["external_id"], item["title"], item["department"],
                            item["location"], item.get("recruitment_type_override") or infer_recruitment_type(full_text),
                            infer_work_nature(item["title"], item["body"]), infer_education(full_text),
                            infer_experience(full_text), item.get("responsibilities", item["body"]),
                            item.get("requirements", item["body"]), item["benefits_raw"],
                            item["salary_raw"], item["url"], item["published_at"], now, now,
                            hash_value, json_dumps(item["raw"]), item.get("headcount"),
                            item.get("talent_program"), item.get("job_category"), item.get("business_group"),
                        ),
                    )
                    job_id = cursor.lastrowid
                    added += 1
                observed_ids.append(job_id)
                conn.execute(
                    "INSERT OR IGNORE INTO job_observations(batch_id, job_id, observed_at) VALUES(?, ?, ?)",
                    (batch_id, job_id, now),
                )
                conn.execute("DELETE FROM job_directions WHERE job_id=? AND version=?", (job_id, load_directions()["version"]))
                for match in job_direction_matches(item["title"], item["body"]):
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO job_directions(job_id, direction_slug, relevance, confidence,
                          rationale, classifier, version) VALUES(?, ?, ?, ?, ?, 'rules', ?)
                        """,
                        (job_id, match["slug"], match["relevance"], match["confidence"], match["rationale"], match["version"]),
                    )
                salary_evidence = parse_salary(item.get("salary_raw") or item["body"])
                if item.get("salary_range") and (item["salary_range"].get("min") or 0) > 0 and (item["salary_range"].get("max") or 0) > 0:
                    sr = item["salary_range"]
                    interval = str(sr.get("interval") or "").lower()
                    lower = sr.get("min")
                    upper = sr.get("max")
                    salary_evidence.append({
                        "lower_value": lower, "upper_value": upper, "currency": sr.get("currency"),
                        "period": interval, "pay_months": None, "salary_type": "unknown",
                        "annual_lower": lower if interval in {"year", "annual"} else None,
                        "annual_upper": upper if interval in {"year", "annual"} else None,
                        "evidence_text": item.get("salary_raw") or json_dumps(sr),
                    })
                for salary in salary_evidence:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO salaries(job_id, lower_value, upper_value, currency, period,
                          pay_months, salary_type, annual_lower, annual_upper, evidence_class, city, level,
                          evidence_text, source_url) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'A', ?, ?, ?, ?)
                        """,
                        (
                            job_id, salary["lower_value"], salary["upper_value"], salary["currency"],
                            salary["period"], salary["pay_months"], salary["salary_type"],
                            salary["annual_lower"], salary["annual_upper"], item["location"],
                            infer_experience(full_text), salary["evidence_text"], item["url"],
                        ),
                    )

            # A failed or partial scan never closes jobs. A complete scan can close only this source's missing records.
            if status == "complete":
                if observed_ids:
                    placeholders = ",".join("?" for _ in observed_ids)
                    conn.execute(
                        f"UPDATE jobs SET status='closed' WHERE source_slug=? AND status='open' AND id NOT IN ({placeholders})",
                        (source["slug"], *observed_ids),
                    )
                else:
                    conn.execute("UPDATE jobs SET status='closed' WHERE source_slug=? AND status='open'", (source["slug"],))
            conn.execute(
                """
                UPDATE scan_batches SET finished_at=?, status=?, reported_total=?, fetched_count=?,
                  relevant_count=?, error=?, raw_path=? WHERE id=?
                """,
                (utcnow(), status, reported_total, len(normalized), relevant, error, str(raw_path) if raw_payload is not None else None, batch_id),
            )
            if status in {"complete", "partial"}:
                conn.execute(
                    "UPDATE sources SET status='connected', last_verified_at=?, last_success_at=?, last_error=? WHERE slug=?",
                    (utcnow(), utcnow(), "最近一次扫描为 partial" if status == "partial" else None, source["slug"]),
                )
            else:
                conn.execute(
                    "UPDATE sources SET status=?, last_verified_at=?, last_error=? WHERE slug=?",
                    (status, utcnow(), error or status, source["slug"]),
                )

        return {"status": status, "added": added, "relevant": relevant, "batch_id": batch_id}
