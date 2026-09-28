from app.services.direction_value import attach_value_scores


def test_direction_value_score_uses_three_visible_components():
    directions = [
        {"slug": "a", "talent_program_jobs": 100, "open_jobs": 80, "papers": 50, "active_authors": 200, "research_scope": {"is_complete": 1, "coverage_ratio": 1}},
        {"slug": "b", "talent_program_jobs": 10, "open_jobs": 8, "papers": 5, "active_authors": 20, "research_scope": {"is_complete": 1, "coverage_ratio": 1}},
    ]
    difficulty = {
        "a": {"score": 30.0, "sample_size": 100, "research_barrier": 2.0, "engineering_barrier": 15.0, "systems_barrier": 5.0, "profile": "test", "note": "test"},
        "b": {"score": 60.0, "sample_size": 10, "research_barrier": 10.0, "engineering_barrier": 25.0, "systems_barrier": 17.0, "profile": "test", "note": "test"},
    }

    scored = attach_value_scores(directions, difficulty)

    assert scored[0]["job_score"] == 100.0
    assert scored[0]["research_heat_score"] == 90.0
    assert scored[0]["value_score"] == 89.0
    assert scored[0]["value_score"] > scored[1]["value_score"]


def test_direction_value_score_handles_empty_inputs():
    row = {"slug": "empty", "talent_program_jobs": 0, "open_jobs": 0, "papers": 0, "active_authors": 0}
    scored = attach_value_scores([row], {})[0]

    assert scored["job_score"] == 0.0
    assert scored["research_heat_score"] == 0.0
    assert scored["personal_difficulty"]["score"] == 50.0
    assert scored["value_score"] == 15.0
