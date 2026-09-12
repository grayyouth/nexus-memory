"""
Nexus Config — centralized configuration management.

Loads settings from nexus_config.json (project root or custom path).
Provides a global config object accessible from all modules.

Usage:
    from core.config import config

    # Read settings
    store_dir = config.store.base_dir
    chunk_size = config.ingestion.max_chunk_chars
    watch_interval = config.watchkeeper.default_interval

    # Update settings at runtime
    config.watchkeeper.default_interval = 600
    config.save()

    # Custom config path
    from core.config import Config
    cfg = Config(config_path="/custom/path/config.json")
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.safe_io import atomic_write_json

logger = logging.getLogger(__name__)

# Default config path (project root)
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "nexus_config.json"

# Default values (matched to nexus_config.json structure)
DEFAULTS: Dict[str, Any] = {
    "version": "0.9.2",
    "store": {
        "base_dir": None,
    },
    "ingestion": {
        "max_chunk_chars": 1200,
    },
    "watchkeeper": {
        "default_interval": 300,
        "auto_start": False,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
        "token": "",
        "mode": "direct",
        "auto_start": False,
        "autoclose_minutes": 360,
        "autoclose_interval_min": 30,
    },
    "summarizer": {
        "max_items_per_bucket": 10,
        "max_item_chars": 300,
        "max_dialog_messages": 5,
    },
    "compressor": {
        "default_level": 1,
        "max_done": 8,
        "max_decisions": 6,
        "max_next": 6,
        "max_other": 4,
        "max_dialog_messages": 6,
        "max_dialog_chars": 220,
        "max_chars": 2400,
    },
    "digest": {
        "max_sessions_per_agent": 6,
        "max_items_per_bucket": 4,
        "max_item_chars": 300,
        "max_decisions": 8,
        "max_chunks": 8,
        "chunk_preview_chars": 140,
    },
    "semantic": {
        "vector_size": 512,
        "cache_size": 128,
        "use_sentence_transformers": False,
    },
    "ocr": {
        "default_engine": None,
        "default_languages": ["eng", "rus"],
    },
    "trust": {
        "trusted_domains": [
            "wikipedia.org", "python.org", "mozilla.org", "microsoft.com",
            "arxiv.org", "github.com", "oracle.com", "redhat.com",
        ],
        "trusted_trust": 1.0,
        "unknown_url_trust": 0.4,
        "local_doc_trust": 0.8,
        "default_trust": 0.5,
        "trust_weight": 0.25,
    },
    "prompt_templates": {
        "context_prompt_max_chunks": 5,
        "context_prompt_max_chars": 1800,
    },
}


class ConfigSection:
    """Read-only view of a config section."""

    def __init__(self, data: Dict[str, Any], path: str = ""):
        self._data = data
        self._path = path

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __repr__(self) -> str:
        return f"ConfigSection({self._data})"


class Config:
    """Nexus configuration manager.

    Loads from nexus_config.json, provides attribute-style access,
    and allows runtime updates with persistence.
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._data: Dict[str, Any] = {}
        self._loaded = False
        self._load()

    def _load(self) -> None:
        """Load config from file, merging with defaults."""
        self._data = json.loads(json.dumps(DEFAULTS))  # deep copy

        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
                self._merge(self._data, user_config)
                self._loaded = True
                logger.info("Config loaded from %s", self._config_path)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load config from %s: %s. Using defaults.",
                               self._config_path, e)
        else:
            logger.debug("Config file not found at %s, using defaults.", self._config_path)

    @staticmethod
    def _merge(target: Dict, source: Dict) -> None:
        """Deep merge source into target."""
        for key, value in source.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                Config._merge(target[key], value)
            else:
                target[key] = value

    # --- Attribute-style access ---

    @property
    def store(self) -> ConfigSection:
        return ConfigSection(self._data.get("store", {}), "store")

    @property
    def ingestion(self) -> ConfigSection:
        return ConfigSection(self._data.get("ingestion", {}), "ingestion")

    @property
    def watchkeeper(self) -> ConfigSection:
        return ConfigSection(self._data.get("watchkeeper", {}), "watchkeeper")

    @property
    def summarizer(self) -> ConfigSection:
        return ConfigSection(self._data.get("summarizer", {}), "summarizer")

    @property
    def digest(self) -> ConfigSection:
        return ConfigSection(self._data.get("digest", {}), "digest")

    @property
    def semantic(self) -> ConfigSection:
        return ConfigSection(self._data.get("semantic", {}), "semantic")

    @property
    def ocr(self) -> ConfigSection:
        return ConfigSection(self._data.get("ocr", {}), "ocr")

    @property
    def prompt_templates(self) -> ConfigSection:
        return ConfigSection(self._data.get("prompt_templates", {}), "prompt_templates")

    @property
    def trust(self) -> ConfigSection:
        return ConfigSection(self._data.get("trust", {}), "trust")

    # --- Convenience getters ---

    @property
    def store_base_dir(self) -> Optional[Path]:
        """Get configured store base directory."""
        bd = self._data.get("store", {}).get("base_dir")
        if bd:
            return Path(bd)
        return None

    @property
    def max_chunk_chars(self) -> int:
        return self._data.get("ingestion", {}).get("max_chunk_chars", 1200)

    @property
    def watchkeeper_interval(self) -> int:
        return self._data.get("watchkeeper", {}).get("default_interval", 300)

    @property
    def watchkeeper_auto_start(self) -> bool:
        return self._data.get("watchkeeper", {}).get("auto_start", False)

    @property
    def server_host(self) -> str:
        return str(self._data.get("server", {}).get("host", "127.0.0.1"))

    @property
    def server_port(self) -> int:
        return int(self._data.get("server", {}).get("port", 8765))

    @property
    def server_token(self) -> str:
        return str(self._data.get("server", {}).get("token", ""))

    @property
    def server_mode(self) -> str:
        return str(self._data.get("server", {}).get("mode", "direct"))

    @property
    def server_auto_start(self) -> bool:
        return bool(self._data.get("server", {}).get("auto_start", False))

    @property
    def autoclose_minutes(self) -> int:
        return int(self._data.get("server", {}).get("autoclose_minutes", 360))

    @property
    def autoclose_interval_min(self) -> int:
        return int(self._data.get("server", {}).get("autoclose_interval_min", 30))

    @property
    def server_url(self) -> str:
        """Base URL of the Nexus daemon (http://host:port)."""
        return f"http://{self.server_host}:{self.server_port}"

    @property
    def ocr_languages(self) -> List[str]:
        return self._data.get("ocr", {}).get("default_languages", ["eng", "rus"])

    @property
    def ocr_engine(self) -> Optional[str]:
        return self._data.get("ocr", {}).get("default_engine")

    @property
    def use_sentence_transformers(self) -> bool:
        return self._data.get("semantic", {}).get("use_sentence_transformers", False)

    @property
    def trust_weight(self) -> float:
        return float(self._data.get("trust", {}).get("trust_weight", 0.25))

    # --- Update methods ---

    def set(self, section: str, key: str, value: Any) -> None:
        """Set a config value."""
        if section not in self._data:
            self._data[section] = {}
        self._data[section][key] = value
        logger.info("Config updated: %s.%s = %s", section, key, value)

    def update(self, **kwargs: Any) -> None:
        """Update multiple config values at once.

        Args:
            **kwargs: Section-key-value pairs as nested dicts.
                      Example: update(store={"base_dir": "/data"}, watchkeeper={"default_interval": 600})
        """
        for section, values in kwargs.items():
            if isinstance(values, dict):
                if section not in self._data:
                    self._data[section] = {}
                for key, value in values.items():
                    self._data[section][key] = value
        logger.info("Config updated: %s", list(kwargs.keys()))

    def save(self) -> None:
        """Save current config to file (atomically — temp + rename)."""
        atomic_write_json(self._config_path, self._data)
        logger.info("Config saved to %s", self._config_path)

    def to_dict(self) -> Dict[str, Any]:
        """Return full config as dictionary."""
        return json.loads(json.dumps(self._data))

    def get_status(self) -> Dict[str, Any]:
        """Return config status for MCP reporting."""
        return {
            "loaded": self._loaded,
            "config_path": str(self._config_path),
            "version": self._data.get("version", "unknown"),
            "settings": {
                "store_base_dir": self.store_base_dir,
                "max_chunk_chars": self.max_chunk_chars,
                "watchkeeper_interval": self.watchkeeper_interval,
                "watchkeeper_auto_start": self.watchkeeper_auto_start,
                "server_url": self.server_url,
                "server_mode": self.server_mode,
                "server_auto_start": self.server_auto_start,
                "server_token_set": bool(self.server_token),
                "autoclose_minutes": self.autoclose_minutes,
                "autoclose_interval_min": self.autoclose_interval_min,
                "ocr_languages": self.ocr_languages,
                "ocr_engine": self.ocr_engine,
                "use_sentence_transformers": self.use_sentence_transformers,
                "trust_weight": self.trust_weight,
            },
        }


# --- Global singleton ---

config = Config()
