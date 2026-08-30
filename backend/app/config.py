from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    aisstream_api_key: str = Field(
        default="",
        validation_alias="AISSTREAM_API_KEY",
    )
    aisstream_url: str = "wss://stream.aisstream.io/v0/stream"
    aisstream_max_mmsis: int = Field(default=50, ge=1, le=1000)
    openwaters_enabled: bool = True
    openwaters_url: str = "wss://ais.openwaters.io/v1/stream"
    openwaters_api_key: str = ""
    openwaters_max_mmsis: int = Field(default=10, ge=1, le=200)
    database_url: str = "postgresql://ais:ais@localhost:5432/ais"
    vessels_config_path: Path = Path("config/vessels.json")
    history_sample_seconds: int = Field(default=60, ge=1, le=3600)
    track_gap_minutes: int = Field(default=45, ge=1, le=10080)
    trip_gap_hours: int = Field(default=6, ge=1, le=168)
    websocket_batch_ms: int = Field(default=0, ge=0, le=5000)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]
