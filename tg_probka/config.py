from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    bot_token: str = Field(..., alias="BOT_TOKEN")
    owner_id: int = Field(..., alias="OWNER_ID")
    db_path: str = Field("bot_database_v6.db", alias="DB_PATH")
    fernet_key: str | None = Field(None, alias="FERNET_KEY")
    timezone: str = Field("UTC", alias="TZ")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    max_chiefs: int = Field(2, alias="MAX_CHIEFS")
    daily_limit_default: int = Field(1, alias="DAILY_LIMIT_DEFAULT")
    low_stock_threshold: int = Field(10, alias="LOW_STOCK_THRESHOLD")
    backup_dir: str = Field("./backups", alias="BACKUP_DIR")
    backup_interval_sec: int = Field(3600, alias="BACKUP_INTERVAL_SEC")
    max_txt_bytes: int = Field(2 * 1024 * 1024, alias="MAX_TXT_BYTES")
    sub_cache_ttl_sec: int = Field(60, alias="SUB_CACHE_TTL_SEC")

    @field_validator("bot_token")
    @classmethod
    def _tok(cls, v: str) -> str:
        if not v or ":" not in v:
            raise ValueError("BOT_TOKEN некорректен")
        return v


settings = Settings()


def setup_logging() -> None:
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter(fmt))
    root.addHandler(stream)

    fh = RotatingFileHandler("bot.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt))
    root.addHandler(fh)

    logging.getLogger("aiogram.event").setLevel(logging.WARNING)