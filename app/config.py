import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    """Конфигурация приложения.
    
    Параметры загружаются из YAML файла и переменных окружения.
    Переменные окружения имеют приоритет над файлом конфигурации.
    """
    telegram_token: str = ""
    telegram_chat_id: int | str = 0
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
        """Загрузить конфигурацию из файла и переменных окружения.
        
        Args:
            path: Путь к файлу конфигурации YAML
            
        Returns:
            Объект Config с загруженными параметрами
            
        Raises:
            ValueError: Если обязательные параметры не установлены
        """
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
                try:
                    if isinstance(current, bool):
                        setattr(cfg, attr, val.lower() in ("true", "1", "yes"))
                    elif isinstance(current, int):
                        # Безопасное преобразование в int с проверкой ошибок
                        try:
                            int_val = int(val)
                            setattr(cfg, attr, int_val)
                        except ValueError as e:
                            raise ValueError(f"Invalid integer value for {env_key}: {val}") from e
                    else:
                        setattr(cfg, attr, val)
                except Exception as e:
                    raise ValueError(f"Failed to parse environment variable {env_key}: {e}") from e

        if not cfg.telegram_token:
            raise ValueError("telegram_token is required (set in config.yml or TELEGRAM_TOKEN env)")

        # Безопасное преобразование chat_id
        chat_id = cfg.telegram_chat_id
        if isinstance(chat_id, str):
            try:
                if chat_id.startswith("-") and chat_id[1:].isdigit():
                    cfg.telegram_chat_id = int(chat_id)
                elif chat_id.isdigit():
                    cfg.telegram_chat_id = int(chat_id)
                else:
                    raise ValueError(f"Invalid chat_id format: {chat_id}")
            except (ValueError, IndexError) as e:
                raise ValueError(f"Failed to parse telegram_chat_id: {e}") from e

        if not cfg.telegram_chat_id:
            raise ValueError("telegram_chat_id is required (set in config.yml or TELEGRAM_CHAT_ID env)")

        return cfg
