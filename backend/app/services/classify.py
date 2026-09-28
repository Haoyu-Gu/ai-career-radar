import re
from typing import Any

from .config_loader import load_directions


AI_CORE = re.compile(
    r"\b(ai|artificial intelligence|machine learning|deep learning|large language model|llm|nlp|computer vision|"
    r"reinforcement learning|generative|multimodal|recommendation|ranking|speech recognition|robotics|research scientist)\b|"
    r"人工智能|机器学习|深度学习|大模型|自然语言|计算机视觉|推荐算法|搜索算法|语音识别|生成模型|多模态|机器人|算法工程",
    re.IGNORECASE,
)
NON_AI = re.compile(
    r"sales|account executive|counsel|recruiter|finance|marketing|legal|human resources|"
    r"executive assistant|operations specialist|payroll|facilities|community manager|"
    r"sales operations|business development|communications|procurement|销售|法务|财务|招聘|行政|采购",
    re.IGNORECASE,
)
TECH_TITLE = re.compile(
    r"engineer|scientist|research|developer|architect|algorithm|data science|data scientist|"
    r"machine learning|ml\b|ai\b|inference|model|robot|算法|工程师|科学家|研究员|研发|模型|机器人",
    re.IGNORECASE,
)


def is_ai_job(title: str, body: str) -> bool:
    joined = f"{title}\n{body}"
    title_hit = bool(AI_CORE.search(title))
    body_hits = len(AI_CORE.findall(body[:12000]))
    if NON_AI.search(title):
        return False
    return title_hit or (bool(TECH_TITLE.search(title)) and body_hits >= 1)


def infer_recruitment_type(text: str) -> str:
    lowered = text.lower()
    # Generic job footers often mention interns, so early-career labels must be title-led.
    headline = lowered.splitlines()[0][:240]
    if re.search(r"\bintern(ship)?\b|实习", headline):
        return "internship"
    if re.search(r"campus|graduate program|new grad|校招|应届", headline):
        return "campus"
    if re.search(r"full[- ]?time|社招|experienced", lowered):
        return "experienced"
    return "unknown"


def infer_work_nature(title: str, body: str) -> str:
    text = f"{title} {body}".lower()
    if re.search(r"research scientist|researcher|研究科学家|研究员", text):
        return "research"
    if re.search(r"infrastructure|platform|distributed|serving|compiler|kernel|基础设施|系统优化", text):
        return "engineering_infra"
    if re.search(r"applied ai|solutions|integration|forward deployed|应用算法|解决方案", text):
        return "application_integration"
    if re.search(r"machine learning|algorithm|算法|research engineer", text):
        return "algorithm_rd"
    return "unknown"


def infer_education(text: str) -> str:
    lowered = text.lower()
    if re.search(r"ph\.?d|doctorate|博士", lowered):
        return "phd"
    if re.search(r"master'?s|硕士", lowered):
        return "master"
    if re.search(r"bachelor'?s|本科", lowered):
        return "bachelor"
    return "unknown"


def infer_experience(text: str) -> str:
    patterns = [
        (r"(?:10|ten)\+?\s*(?:years?|年)", "10y+"),
        (r"(?:[5-9]|five|six|seven|eight|nine)\+?\s*(?:years?|年)", "5-9y"),
        (r"(?:[2-4]|two|three|four)\+?\s*(?:years?|年)", "2-4y"),
        (r"(?:1|one)\+?\s*(?:years?|年)|new grad|应届", "0-1y"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return label
    return "unknown"


def _term_present(term: str, lowered: str) -> bool:
    normalized = term.lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9 .+/_-]*", normalized):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", lowered))
    return normalized in lowered


def direction_matches(text: str) -> list[dict[str, Any]]:
    doc = load_directions()
    lowered = text.lower()
    matches: list[dict[str, Any]] = []
    for direction in doc["directions"]:
        includes = [term for term in direction.get("include", []) if _term_present(term, lowered)]
        excludes = [term for term in direction.get("exclude", []) if _term_present(term, lowered)]
        if includes and not excludes:
            confidence = min(0.95, 0.58 + 0.09 * len(includes))
            matches.append(
                {
                    "slug": direction["slug"],
                    "relevance": "core" if len(includes) >= 2 else "secondary",
                    "confidence": round(confidence, 2),
                    "rationale": "规则命中：" + "、".join(includes[:4]),
                    "version": doc["version"],
                }
            )
    return matches


def job_direction_matches(title: str, body: str) -> list[dict[str, Any]]:
    """Treat a title hit as strong; a single body-only hit remains a possible match."""
    matches = direction_matches(f"{title}\n{body}")
    doc = load_directions()
    title_lower = title.lower()
    by_slug = {item["slug"]: item for item in doc["directions"]}
    for match in matches:
        direction = by_slug[match["slug"]]
        title_hits = [term for term in direction.get("include", []) if _term_present(term, title_lower)]
        if title_hits:
            match["relevance"] = "core"
            match["confidence"] = max(match["confidence"], .86)
            match["rationale"] = "标题命中：" + "、".join(title_hits[:4])
    return matches
