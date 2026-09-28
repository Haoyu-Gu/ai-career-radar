from app.services.classify import direction_matches


def test_short_english_keywords_use_word_boundaries() -> None:
    slugs = {item["slug"] for item in direction_matches("Storage program manager for average latency")}
    assert "agents_rag" not in slugs


def test_rag_keyword_still_matches_as_a_term() -> None:
    slugs = {item["slug"] for item in direction_matches("Engineer building RAG and tool use systems")}
    assert "agents_rag" in slugs
