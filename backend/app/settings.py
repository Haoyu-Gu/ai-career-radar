from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8787
    database_path: str = "data/ai_trends.db"
    request_timeout_seconds: float = 25.0
    min_salary_sample: int = 10
    paper_years: int = 5
    openalex_max_per_direction: int = 1000
    openalex_max_per_year: int = 200
    openalex_page_size: int = 100
    crossref_max_per_direction: int = 500
    crossref_max_per_year: int = 100
    crossref_page_size: int = 100
    crossref_mailto: str | None = None
    arxiv_categories: str = "cs.AI,cs.CL,cs.CV,cs.LG,cs.RO,cs.IR,eess.AS"
    max_jobs_per_source: int = 1000
    job_drop_alert_ratio: float = 0.5
    auto_refresh_enabled: bool = True
    auto_refresh_hours: int = 24
    scheduler_check_minutes: int = 30
    usd_cny_fallback: float = 6.713233
    usd_cny_fallback_date: str = "2026-09-25"
    openalex_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5-mini"

    @property
    def db_path(self) -> Path:
        path = Path(self.database_path)
        return path if path.is_absolute() else ROOT / path

    @property
    def config_dir(self) -> Path:
        return ROOT / "backend" / "config"

    @property
    def raw_dir(self) -> Path:
        return ROOT / "data" / "raw"

    @property
    def arxiv_category_list(self) -> list[str]:
        return [item.strip() for item in self.arxiv_categories.split(",") if item.strip()]


settings = Settings()
