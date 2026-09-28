from app.services.salary import parse_salary


def test_cn_monthly_with_pay_months() -> None:
    result = parse_salary("薪资 30—50K·16薪，另有期权")
    assert len(result) == 1
    salary = result[0]
    assert salary["currency"] == "CNY"
    assert salary["period"] == "month"
    assert salary["pay_months"] == 16
    assert salary["annual_lower"] == 480_000
    assert salary["annual_upper"] == 800_000


def test_monthly_without_months_is_not_annualized() -> None:
    salary = parse_salary("月薪 20-35K")[0]
    assert salary["annual_lower"] is None
    assert salary["annual_upper"] is None


def test_daily_internship_is_not_annualized() -> None:
    salary = parse_salary("实习补贴 300-500元/天")[0]
    assert salary["period"] == "day"
    assert salary["annual_lower"] is None

