"""
Tests for nexus_cli.py — CLI utility for Nexus.
"""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Nexus"))

from core.nexus_core import Nexus
from core.ingestion import IngestionPipeline


class TestCLICommands:
    """Test CLI command functions directly."""

    @pytest.fixture
    def nm(self, nexus_store):
        return Nexus(base_dir=nexus_store)

    @pytest.fixture
    def pipeline(self, nexus_instance, nexus_store):
        raw_dir = nexus_store / "input_docs" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        return IngestionPipeline(nexus_instance, raw_dir=raw_dir)

    def test_cmd_add(self, nm, capsys):
        """Test add command."""
        from nexus_cli import cmd_add
        import argparse
        
        args = argparse.Namespace(
            store=nm.base_dir,
            content="test content",
            tags=["#test", "#cli"],
            project="TestProject",
            source="https://example.com",
            agent="test_agent",
        )
        
        cmd_add(args)
        captured = capsys.readouterr()
        
        assert "Added chunk:" in captured.out
        assert "Tags: #test, #cli" in captured.out
        assert "Project: TestProject" in captured.out
        assert "Source: https://example.com" in captured.out
        
        # Verify chunk was actually added
        chunks_index = nm._read_json(nm.chunks_index_file) or []
        assert len(chunks_index) >= 1

    def test_cmd_search_keyword(self, nm, capsys):
        """Test keyword search."""
        from nexus_cli import cmd_search
        import argparse
        
        nm.add_chunk("hello world test", ["#tag"])
        
        args = argparse.Namespace(
            store=nm.base_dir,
            query="hello",
            tags=None,
            project=None,
            agent=None,
            semantic=False,
            top_k=5,
        )
        
        cmd_search(args)
        captured = capsys.readouterr()
        
        assert "Found 1 result" in captured.out

    def test_cmd_search_empty(self, nm, capsys):
        """Test search with no results."""
        from nexus_cli import cmd_search
        import argparse
        
        args = argparse.Namespace(
            store=nm.base_dir,
            query="xyznotfound",
            tags=None,
            project=None,
            agent=None,
            semantic=False,
            top_k=5,
        )
        
        cmd_search(args)
        captured = capsys.readouterr()
        
        assert "No results found" in captured.out

    def test_cmd_search_semantic(self, nm, capsys):
        """Test semantic search."""
        from nexus_cli import cmd_search
        import argparse
        
        nm.add_chunk("hello world semantic search", ["#tag"])
        
        args = argparse.Namespace(
            store=nm.base_dir,
            query="hello world",
            tags=None,
            project=None,
            agent=None,
            semantic=True,
            top_k=5,
        )
        
        cmd_search(args)
        captured = capsys.readouterr()
        
        # Should find at least one result
        assert "No semantic matches" not in captured.out

    def test_cmd_context_new_agent(self, nm, capsys):
        """Test context for new agent."""
        from nexus_cli import cmd_context
        import argparse
        
        args = argparse.Namespace(
            store=nm.base_dir,
            agent="new_agent_xyz",
            project=None,
        )
        
        cmd_context(args)
        captured = capsys.readouterr()
        
        assert "No prior context found" in captured.out

    def test_cmd_context_with_summary(self, nm, capsys):
        """Test context with existing summary."""
        from nexus_cli import cmd_context
        import argparse
        
        nm.store_session_summary("test_agent", "# Test Summary\nDone: task1")
        
        args = argparse.Namespace(
            store=nm.base_dir,
            agent="test_agent",
            project=None,
        )
        
        cmd_context(args)
        captured = capsys.readouterr()
        
        assert "Test Summary" in captured.out

    def test_cmd_summary_new_agent(self, nm, capsys):
        """Test summary for new agent."""
        from nexus_cli import cmd_summary
        import argparse
        
        args = argparse.Namespace(
            store=nm.base_dir,
            agent="nonexistent_agent",
        )
        
        cmd_summary(args)
        captured = capsys.readouterr()
        
        assert "Error:" in captured.out or "not found" in captured.out.lower()

    def test_cmd_summary_existing(self, nm, capsys):
        """Test summary for agent with archive."""
        from nexus_cli import cmd_summary
        import argparse
        
        nm.archive_session("test_agent", {"actions": ["done task"], "decisions": ["use blake3"]})
        
        args = argparse.Namespace(
            store=nm.base_dir,
            agent="test_agent",
        )
        
        cmd_summary(args)
        captured = capsys.readouterr()
        
        assert "Summary generated" in captured.out

    def test_cmd_ingest(self, nm, capsys, pipeline):
        """Test ingestion command."""
        from nexus_cli import cmd_ingest
        import argparse
        
        # Create test files
        (pipeline.raw_dir / "test.md").write_text("# Test", encoding="utf-8")
        (pipeline.raw_dir / "test.json").write_text('{"x":1}', encoding="utf-8")
        
        args = argparse.Namespace(
            store=nm.base_dir,
        )
        
        cmd_ingest(args)
        captured = capsys.readouterr()
        
        assert "Scanned:" in captured.out
        assert "Ingested:" in captured.out

    def test_cmd_decisions_empty(self, nm, capsys):
        """Test decisions for project with no decisions."""
        from nexus_cli import cmd_decisions
        import argparse
        
        args = argparse.Namespace(
            store=nm.base_dir,
            project="nonexistent_project",
        )
        
        cmd_decisions(args)
        captured = capsys.readouterr()
        
        assert "No decisions found" in captured.out

    def test_cmd_decisions_with_data(self, nm, capsys):
        """Test decisions for project with decisions."""
        from nexus_cli import cmd_decisions
        import argparse
        
        nm.add_joint_decision("test_project", {
            "decision": "use blake3",
            "by_agents": ["a", "b"],
            "reason": "fast",
        })
        
        args = argparse.Namespace(
            store=nm.base_dir,
            project="test_project",
        )
        
        cmd_decisions(args)
        captured = capsys.readouterr()
        
        assert "use blake3" in captured.out
