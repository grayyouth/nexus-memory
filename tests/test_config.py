"""
Tests for core/config.py - Configuration management.
"""
import pytest
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import Config, config, DEFAULTS


class TestConfigInit:
    """Test Config initialization."""

    def test_default_init(self, tmp_path):
        """Config uses DEFAULTS when no config file is found."""
        cfg = Config(config_path=tmp_path / "does_not_exist.json")
        assert cfg._loaded is False
        assert cfg.max_chunk_chars == 1200
        assert cfg.watchkeeper_interval == 300

    def test_custom_config_path(self, tmp_path):
        """Config loads from custom path."""
        config_file = tmp_path / "custom_config.json"
        config_data = {
            "version": "1.0.0",
            "ingestion": {"max_chunk_chars": 2000},
            "watchkeeper": {"default_interval": 600},
        }
        config_file.write_text(json.dumps(config_data), encoding="utf-8")
        
        cfg = Config(config_path=config_file)
        assert cfg._loaded is True
        assert cfg.max_chunk_chars == 2000
        assert cfg.watchkeeper_interval == 600

    def test_missing_config_file(self, tmp_path):
        """Config uses defaults when file doesn't exist."""
        cfg = Config(config_path=tmp_path / "nonexistent.json")
        assert cfg._loaded is False
        assert cfg.max_chunk_chars == 1200

    def test_invalid_json(self, tmp_path):
        """Config uses defaults when JSON is invalid."""
        config_file = tmp_path / "bad_config.json"
        config_file.write_text("{invalid json!!!", encoding="utf-8")
        
        cfg = Config(config_path=config_file)
        assert cfg._loaded is False
        assert cfg.max_chunk_chars == 1200


class TestConfigAccess:
    """Test attribute-style config access."""

    def test_store_section(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"store": {"base_dir": "/custom/store"}}), encoding="utf-8")
        
        cfg = Config(config_path=config_file)
        assert cfg.store_base_dir == Path("/custom/store")

    def test_ingestion_section(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"ingestion": {"max_chunk_chars": 1500}}), encoding="utf-8")
        
        cfg = Config(config_path=config_file)
        assert cfg.max_chunk_chars == 1500

    def test_watchkeeper_section(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "watchkeeper": {"default_interval": 900, "auto_start": True}
        }), encoding="utf-8")
        
        cfg = Config(config_path=config_file)
        assert cfg.watchkeeper_interval == 900
        assert cfg.watchkeeper_auto_start is True

    def test_ocr_section(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "ocr": {"default_languages": ["eng", "deu"], "default_engine": "pytesseract"}
        }), encoding="utf-8")
        
        cfg = Config(config_path=config_file)
        assert cfg.ocr_languages == ["eng", "deu"]
        assert cfg.ocr_engine == "pytesseract"

    def test_summarizer_section(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "summarizer": {"max_items_per_bucket": 20, "max_item_chars": 500}
        }), encoding="utf-8")
        
        cfg = Config(config_path=config_file)
        assert cfg.summarizer.get("max_items_per_bucket") == 20
        assert cfg.summarizer.get("max_item_chars") == 500

    def test_semantic_section(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "semantic": {"vector_size": 256, "cache_size": 64, "use_sentence_transformers": True}
        }), encoding="utf-8")
        
        cfg = Config(config_path=config_file)
        assert cfg.semantic.get("vector_size") == 256
        assert cfg.semantic.get("cache_size") == 64
        assert cfg.use_sentence_transformers is True


class TestConfigUpdate:
    """Test config update and persistence."""

    def test_set_single_value(self, tmp_path):
        config_file = tmp_path / "config.json"
        cfg = Config(config_path=config_file)
        
        cfg.set("watchkeeper", "default_interval", 1200)
        assert cfg.watchkeeper_interval == 1200

    def test_save_and_reload(self, tmp_path):
        config_file = tmp_path / "config.json"
        cfg = Config(config_path=config_file)
        
        cfg.set("ingestion", "max_chunk_chars", 2500)
        cfg.save()
        
        # Reload
        cfg2 = Config(config_path=config_file)
        assert cfg2.max_chunk_chars == 2500

    def test_update_multiple(self, tmp_path):
        config_file = tmp_path / "config.json"
        cfg = Config(config_path=config_file)
        
        cfg.update(
            watchkeeper={"default_interval": 1800},
            ocr={"default_languages": ["eng", "fra"]},
        )
        
        assert cfg.watchkeeper_interval == 1800
        assert cfg.ocr_languages == ["eng", "fra"]

    def test_update_and_save_reload(self, tmp_path):
        config_file = tmp_path / "config.json"
        cfg = Config(config_path=config_file)
        
        cfg.update(
            ingestion={"max_chunk_chars": 3000},
            watchkeeper={"default_interval": 600},
        )
        cfg.save()
        
        cfg2 = Config(config_path=config_file)
        assert cfg2.max_chunk_chars == 3000
        assert cfg2.watchkeeper_interval == 600


class TestConfigConvenience:
    """Test convenience getters."""

    def test_store_base_dir_none(self, tmp_path):
        cfg = Config(config_path=tmp_path / "does_not_exist.json")
        assert cfg.store_base_dir is None

    def test_store_base_dir_set(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"store": {"base_dir": "/data/nexus"}}), encoding="utf-8")
        
        cfg = Config(config_path=config_file)
        assert cfg.store_base_dir == Path("/data/nexus")

    def test_ocr_languages_default(self):
        cfg = Config()
        assert cfg.ocr_languages == ["eng", "rus"]

    def test_ocr_engine_default(self):
        cfg = Config()
        assert cfg.ocr_engine is None

    def test_use_sentence_transformers_default(self):
        cfg = Config()
        assert cfg.use_sentence_transformers is False


class TestConfigStatus:
    """Test config status reporting."""

    def test_get_status(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "version": "1.0.0",
            "store": {"base_dir": "/custom"},
            "ingestion": {"max_chunk_chars": 1500},
            "watchkeeper": {"default_interval": 600, "auto_start": True},
            "ocr": {"default_languages": ["eng"]},
        }), encoding="utf-8")
        
        cfg = Config(config_path=config_file)
        status = cfg.get_status()
        
        assert status["loaded"] is True
        assert status["version"] == "1.0.0"
        assert status["settings"]["store_base_dir"] == Path("/custom")
        assert status["settings"]["max_chunk_chars"] == 1500
        assert status["settings"]["watchkeeper_interval"] == 600
        assert status["settings"]["watchkeeper_auto_start"] is True
        assert status["settings"]["ocr_languages"] == ["eng"]

    def test_get_status_defaults(self, tmp_path):
        cfg = Config(config_path=tmp_path / "does_not_exist.json")
        status = cfg.get_status()
        
        # isolated Config -> pure defaults
        assert status["settings"]["max_chunk_chars"] == 1200
        assert status["settings"]["watchkeeper_interval"] == 300


class TestConfigToDict:
    """Test config serialization."""

    def test_to_dict(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "version": "1.0.0",
            "ingestion": {"max_chunk_chars": 1500},
        }), encoding="utf-8")
        
        cfg = Config(config_path=config_file)
        data = cfg.to_dict()
        
        assert data["version"] == "1.0.0"
        assert data["ingestion"]["max_chunk_chars"] == 1500
        assert "watchkeeper" in data
        assert "ocr" in data


class TestConfigMerge:
    """Test deep merge behavior."""

    def test_partial_override(self, tmp_path):
        """Partial config overrides only specified keys."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"ingestion": {"max_chunk_chars": 1500}}), encoding="utf-8")
        
        cfg = Config(config_path=config_file)
        # ingestion overridden
        assert cfg.max_chunk_chars == 1500
        # watchkeeper still default
        assert cfg.watchkeeper_interval == 300

    def test_nested_override(self, tmp_path):
        """Nested config values override correctly."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "summarizer": {
                "max_items_per_bucket": 20,
                "max_item_chars": 500,
            }
        }), encoding="utf-8")
        
        cfg = Config(config_path=config_file)
        assert cfg.summarizer.get("max_items_per_bucket") == 20
        assert cfg.summarizer.get("max_item_chars") == 500
        # max_dialog_messages still default
        assert cfg.summarizer.get("max_dialog_messages") == 5
