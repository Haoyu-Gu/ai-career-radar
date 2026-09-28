from dataclasses import dataclass
from math import sqrt
from statistics import mean


@dataclass
class ForecastDecision:
    enabled: bool
    reason: str
    method: str | None = None
    mae: float | None = None
    baseline_mae: float | None = None
    values: list[float] | None = None
    lower: list[float] | None = None
    upper: list[float] | None = None
    confidence: str | None = None
    basis: str | None = None


def _mae(actual: list[float], predicted: list[float]) -> float:
    return mean(abs(a - p) for a, p in zip(actual, predicted))


def _ses(history: list[float], alpha: float = 0.35) -> float:
    level = history[0]
    for value in history[1:]:
        level = alpha * value + (1 - alpha) * level
    return level


def _linear_forecast(clean: list[float], horizon: int) -> tuple[list[float], list[float], list[float], float]:
    window = clean[-min(24, len(clean)):]
    n = len(window)
    x_mean = (n - 1) / 2
    y_mean = mean(window)
    denominator = sum((i - x_mean) ** 2 for i in range(n)) or 1
    slope = sum((i - x_mean) * (value - y_mean) for i, value in enumerate(window)) / denominator
    # Damping keeps a short noisy sample from exploding while preserving its direction.
    slope *= .72
    fitted = [y_mean + slope * (i - x_mean) for i in range(n)]
    residual = sqrt(mean((actual - predicted) ** 2 for actual, predicted in zip(window, fitted)))
    values = [max(0.0, y_mean + slope * (n - 1 + step - x_mean)) for step in range(1, horizon + 1)]
    spread = max(1.0, residual * 1.64)
    lower = [max(0.0, value - spread) for value in values]
    upper = [value + spread for value in values]
    return values, lower, upper, residual


def forecast_series(
    values: list[float | None], horizon: int = 3, stable: bool = True,
    allow_exploratory: bool = False,
) -> ForecastDecision:
    if any(value is None for value in values):
        return ForecastDecision(False, "序列包含缺失月份；缺失值不会按 0 填充")
    clean = [float(value) for value in values if value is not None]
    if not stable and allow_exploratory and len(clean) >= 12:
        predicted, lower, upper, residual = _linear_forecast(clean, horizon)
        return ForecastDecision(
            True,
            "这是按已抓到的论文样本推算的热度方向，只适合看升降，不代表未来岗位数。",
            "damped_linear_trend",
            residual,
            None,
            predicted,
            lower,
            upper,
            "low",
            "research_sample",
        )
    if not stable:
        return ForecastDecision(False, "数据口径还在变化，暂时无法做可靠的岗位预测")
    if len(clean) < 24:
        return ForecastDecision(False, f"仅有 {len(clean)} 个完整月，至少需要 24 个")
    if sum(value > 0 for value in clean) < 18:
        return ForecastDecision(False, "非零月份不足，暂不预测")
    start = max(12, len(clean) - 8)
    actual: list[float] = []
    naive: list[float] = []
    ses: list[float] = []
    for index in range(start, len(clean)):
        history = clean[:index]
        actual.append(clean[index])
        naive.append(history[-1])
        ses.append(_ses(history))
    baseline_mae = _mae(actual, naive)
    ses_mae = _mae(actual, ses)
    if ses_mae >= baseline_mae:
        return ForecastDecision(
            True, "指数平滑未优于最近值基线，展示朴素基线", "naive", baseline_mae, baseline_mae,
            [clean[-1]] * horizon, None, None, "medium", "stable_monthly_series",
        )
    next_value = max(0.0, _ses(clean))
    return ForecastDecision(
        True, "通过滚动回测", "simple_exponential_smoothing", ses_mae, baseline_mae,
        [next_value] * horizon, None, None, "medium", "stable_monthly_series",
    )
