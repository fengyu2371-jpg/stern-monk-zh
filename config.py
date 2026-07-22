from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


class ConfigError(RuntimeError):
    """Railway 執行設定不完整或格式錯誤。"""


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def _read_bool(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw = str(values.get(name, "")).strip().lower()
    if not raw:
        return default
    if raw in TRUE_VALUES:
        return True
    if raw in FALSE_VALUES:
        return False
    raise ConfigError(f"{name} 必須是 true 或 false。")


def _read_int(
    values: Mapping[str, str],
    name: str,
    default: int | None,
    *,
    minimum: int = 0,
) -> int | None:
    raw = str(values.get(name, "")).strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必須是純數字。") from exc
    if parsed < minimum:
        raise ConfigError(f"{name} 不可小於 {minimum}。")
    return parsed


@dataclass(frozen=True)
class Settings:
    monk_token: str
    guild_id: int | None
    monk_channel_id: int | None
    ai_enabled: bool
    ai_confession_enabled: bool
    ai_oracle_enabled: bool
    openai_api_key: str
    openai_model: str
    ai_daily_limit: int
    ai_max_output_tokens: int
    oracle_max_output_tokens: int
    monk_db_path: str

    @property
    def ai_available(self) -> bool:
        return self.ai_enabled and bool(self.openai_api_key)

    @property
    def confession_ai_available(self) -> bool:
        return self.ai_available and self.ai_confession_enabled

    @property
    def oracle_ai_available(self) -> bool:
        return self.ai_available and self.ai_oracle_enabled

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "Settings":
        guild_id = _read_int(values, "GUILD_ID", None, minimum=1)
        monk_channel_id = _read_int(values, "MONK_CHANNEL_ID", None, minimum=1)
        ai_daily_limit = _read_int(values, "AI_DAILY_LIMIT", 5, minimum=0)
        ai_max_output_tokens = _read_int(
            values, "AI_MAX_OUTPUT_TOKENS", 180, minimum=50
        )
        oracle_max_output_tokens = _read_int(
            values, "ORACLE_MAX_OUTPUT_TOKENS", 700, minimum=100
        )

        return cls(
            monk_token=str(values.get("MONK_TOKEN", "")).strip(),
            guild_id=guild_id,
            monk_channel_id=monk_channel_id,
            ai_enabled=_read_bool(values, "AI_ENABLED", False),
            ai_confession_enabled=_read_bool(
                values, "AI_CONFESSION_ENABLED", True
            ),
            ai_oracle_enabled=_read_bool(
                values, "AI_ORACLE_ENABLED", True
            ),
            openai_api_key=str(values.get("OPENAI_API_KEY", "")).strip(),
            openai_model=str(values.get("OPENAI_MODEL", "gpt-5-nano")).strip()
            or "gpt-5-nano",
            ai_daily_limit=int(ai_daily_limit),
            ai_max_output_tokens=int(ai_max_output_tokens),
            oracle_max_output_tokens=int(oracle_max_output_tokens),
            monk_db_path=str(
                values.get("MONK_DB_PATH", "/app/storage/monk.db")
            ).strip() or "/app/storage/monk.db",
        )

    @classmethod
    def from_env(cls) -> "Settings":
        return cls.from_mapping(os.environ)

    def validate_runtime(self) -> None:
        if not self.monk_token:
            raise ConfigError(
                "找不到 MONK_TOKEN。請到 Railway → Variables 設定修士 Bot Token。"
            )
        if self.monk_channel_id is None:
            raise ConfigError(
                "找不到 MONK_CHANNEL_ID。請設定允許修士回覆的 Discord 頻道 ID。"
            )


def is_allowed_channel(channel_id: int | None, allowed_channel_id: int | None) -> bool:
    return (
        allowed_channel_id is not None
        and channel_id is not None
        and int(channel_id) == int(allowed_channel_id)
    )
