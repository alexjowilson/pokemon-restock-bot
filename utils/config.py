"""Loads config/config.yaml and secrets from .env into one typed object."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    retailer: str
    url: str
    extra: dict = field(default_factory=dict)  # retailer-specific fields (sku, tcin, ...)

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "retailer": self.retailer, "url": self.url, **self.extra}


@dataclass(frozen=True)
class Settings:
    discord_token: str
    app_id: int
    alert_channel_id: int
    guild_id: int | None
    health_channel_id: int | None
    alert_role_id: int | None
    check_interval_seconds: int
    products: list[Product]


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable {name} (set it in .env)")
    return value


def load_settings(config_path: Path = DEFAULT_CONFIG_PATH) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    raw = yaml.safe_load(config_path.read_text()) or {}

    discord_cfg = raw.get("discord", {})
    # Env var wins over YAML so the channel can differ between dev and prod servers.
    channel_id = os.getenv("ALERT_CHANNEL_ID") or discord_cfg.get("alert_channel_id")
    if not channel_id:
        raise RuntimeError("Set discord.alert_channel_id in config.yaml or ALERT_CHANNEL_ID in .env")
    guild_id = os.getenv("GUILD_ID") or discord_cfg.get("guild_id")
    health_channel_id = os.getenv("HEALTH_CHANNEL_ID") or discord_cfg.get("health_channel_id")
    alert_role_id = os.getenv("ALERT_ROLE_ID") or discord_cfg.get("alert_role_id")

    products = []
    seen_ids = set()
    for p in raw.get("products", []):
        p = dict(p)
        pid = p.pop("id")
        if pid in seen_ids:
            raise RuntimeError(f"Duplicate product id in config: {pid}")
        seen_ids.add(pid)
        products.append(Product(id=pid, name=p.pop("name"), retailer=p.pop("retailer"),
                                url=p.pop("url"), extra=p))

    return Settings(
        discord_token=_require_env("DISCORD_TOKEN"),
        app_id=int(_require_env("APP_ID")),
        alert_channel_id=int(channel_id),
        guild_id=int(guild_id) if guild_id else None,
        health_channel_id=int(health_channel_id) if health_channel_id else None,
        alert_role_id=int(alert_role_id) if alert_role_id else None,
        check_interval_seconds=max(30, int(raw.get("check_interval_seconds", 60))),
        products=products,
    )
