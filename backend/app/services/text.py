import html
import re


TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def plain_text(value: str | None) -> str:
    if not value:
        return ""
    # Some ATS responses entity-encode an entire HTML fragment. Decode first, then strip tags.
    decoded = value
    for _ in range(3):
        next_value = html.unescape(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return SPACE_RE.sub(" ", TAG_RE.sub(" ", decoded)).strip()


def excerpt(value: str | None, limit: int = 500) -> str:
    text = plain_text(value)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
