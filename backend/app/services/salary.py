import re
from dataclasses import asdict, dataclass


@dataclass
class SalaryEvidence:
    lower_value: float | None
    upper_value: float | None
    currency: str | None
    period: str | None
    pay_months: float | None
    salary_type: str
    annual_lower: float | None
    annual_upper: float | None
    evidence_text: str


CN_MONTHLY = re.compile(
    r"(?P<low>\d+(?:\.\d+)?)\s*[-—–~至]\s*(?P<high>\d+(?:\.\d+)?)\s*[kK千]\s*(?:[·x×*]\s*(?P<months>\d+(?:\.\d+)?)\s*薪?)?"
)
USD_ANNUAL = re.compile(
    r"(?:USD|US\$|\$)\s*(?P<low>\d{2,3}(?:,\d{3})+|\d{5,6})\s*[-—–~至]\s*(?:USD|US\$|\$)?\s*(?P<high>\d{2,3}(?:,\d{3})+|\d{5,6})",
    re.IGNORECASE,
)
DAILY = re.compile(r"(?P<low>\d+(?:\.\d+)?)\s*[-—–~至]\s*(?P<high>\d+(?:\.\d+)?)\s*元\s*/?\s*(?:天|日)")


def parse_salary(text: str | None) -> list[dict]:
    if not text:
        return []
    found: list[SalaryEvidence] = []
    for match in CN_MONTHLY.finditer(text):
        low = float(match.group("low")) * 1000
        high = float(match.group("high")) * 1000
        months = float(match.group("months")) if match.group("months") else None
        found.append(
            SalaryEvidence(
                low, high, "CNY", "month", months, "unknown",
                low * months if months else None,
                high * months if months else None,
                match.group(0),
            )
        )
    for match in USD_ANNUAL.finditer(text):
        low = float(match.group("low").replace(",", ""))
        high = float(match.group("high").replace(",", ""))
        found.append(SalaryEvidence(low, high, "USD", "year", None, "base", low, high, match.group(0)))
    for match in DAILY.finditer(text):
        low = float(match.group("low"))
        high = float(match.group("high"))
        found.append(SalaryEvidence(low, high, "CNY", "day", None, "unknown", None, None, match.group(0)))
    unique = {item.evidence_text: asdict(item) for item in found}
    return list(unique.values())


def find_salary_snippet(text: str) -> str | None:
    for pattern in (CN_MONTHLY, USD_ANNUAL, DAILY):
        match = pattern.search(text)
        if match:
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 140)
            return text[start:end].strip()
    return None

