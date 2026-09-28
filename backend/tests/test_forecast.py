from app.services.forecast import forecast_series


def test_forecast_requires_24_complete_months() -> None:
    decision = forecast_series([10.0] * 23)
    assert not decision.enabled
    assert "24" in decision.reason


def test_forecast_rejects_missing_months() -> None:
    decision = forecast_series([10.0] * 24 + [None])
    assert not decision.enabled
    assert "缺失" in decision.reason


def test_forecast_does_not_claim_unstable_series() -> None:
    decision = forecast_series([10.0] * 30, stable=False)
    assert not decision.enabled
    assert "口径" in decision.reason

