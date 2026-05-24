import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    telegram_token: str = ""
    telegram_chat_id: int = 0
    frigate_url: str = "http://localhost:5000"
    poll_interval: int = 5
    event_limit: int = 50
    state_file: str = "/data/state.json"
    include_cameras: list[str] = field(default_factory=lambda: ["all"])
    exclude_cameras: list[str] = field(default_factory=list)
    include_labels: list[str] = field(default_factory=lambda: ["person"])
    exclude_labels: list[str] = field(default_factory=list)
    send_snapshot: bool = True
    send_video: bool = False
    debug: bool = False

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        cfg = cls()

        if path and Path(path).exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            for key, val in data.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, val)

        env_map = {
            "TELEGRAM_TOKEN": "telegram_token",
            "TELEGRAM_CHAT_ID": "telegram_chat_id",
            "FRIGATE_URL": "frigate_url",
            "POLL_INTERVAL": "poll_interval",
            "DEBUG": "debug",
        }
        for env_key, attr in env_map.items():
            if env_key in os.environ:
                val: str = os.environ[env_key]
                current = getattr(cfg, attr)
                if isinstance(current, bool):
                    setattr(cfg, attr, val.lower() in ("true", "1", "yes"))
                elif isinstance(current, int):
                    setattr(cfg, attr, int(val))
                else:
                    setattr(cfg, attr, val)

        return cfg
