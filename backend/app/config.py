from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]
ALLOWED_COURT_AUTO_FETCH_MODES = ("confirm_before_sign", "auto_non_judgment", "auto_all")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "LawTeam-OS"
    timezone: str = "Asia/Shanghai"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Database
    database_url: str = "postgresql+psycopg://lawteam:lawteam_dev_password@localhost:5432/lawteam"

    # Security / JWT
    jwt_secret_key: str = "lawteam-local-dev-jwt-secret-please-rotate-in-prod"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080

    # File Bridge
    cases_root: str = str(ROOT_DIR / "data" / "cases")

    # Court fetching & auto-archiving policy
    court_auto_fetch_mode: Literal[
        "confirm_before_sign", "auto_non_judgment", "auto_all"
    ] = "confirm_before_sign"
    auto_archive_enabled: bool = True
    frontend_base_url: str = "http://127.0.0.1:5173"
    public_api_url: str = "http://127.0.0.1:8000"

    # Enterprise WeChat & Personal WeChat Push Gateways
    wecom_corp_id: str = ""
    wecom_agent_id: str = ""
    wecom_secret: str = ""
    wecom_callback_token: str = ""
    wecom_encoding_aes_key: str = ""
    wecom_dry_run: bool = True
    wxpusher_app_token: str = ""
    enable_reminder_scheduler: bool = True
    reminder_poll_interval_seconds: int = 60

    # Universal LLM config (optional fallback for factual extraction only)
    llm_provider: Literal["none", "openai_compatible", "anthropic"] = "none"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 30

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def cases_root_path(self) -> Path:
        path = Path(self.cases_root)
        if not path.is_absolute():
            path = (ROOT_DIR / path).resolve()
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
