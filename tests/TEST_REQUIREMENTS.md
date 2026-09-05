# Test Requirements for Nexus

## File: tests/test_nexus_core.py

### Fixtures
```python
import pytest, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Nexus"))

class TestNexusCore:
    @pytest.fixture
    def nm(self, nexus_store):
        from core.nexus_core import Nexus
        return Nexus(base_dir=nexus_store)
```

### Test Classes

#### test_nexus_creates_directories
- Create Nexus with tmp_path
- Verify directories exist: input_docs/raw, input_docs/processed, main_library, _index, sessions/archived_sessions, sessions/session_summaries, sessions/cross_sessions

#### test_nexus_custom_base_dir
- Create Nexus(base_dir=tmp_path / "custom")
- Verify base_dir is correct

#### test_add_chunk_creates_file
- nm.add_chunk("test content", ["#tag"])
- Verify .md file exists in main_library/general/snippets/
- Verify file contains front-matter (tags, created_at)
- Verify file contains content

#### test_add_chunk_updates_index
- Before: len(chunks_index) == 0
- nm.add_chunk("test", ["#test"])
- After: len(chunks_index) == 1
- Verify entry has id, tags, source_file, created_at

#### test_add_chunk_updates_tags
- nm.add_chunk("test", ["#alpha", "#beta"])
- Verify tags.json has "#alpha" and "#beta" with chunk id

#### test_add_chunk_idempotent
- nm.add_chunk("same content", ["#tag"]) -> id1
- nm.add_chunk("same content", ["#tag"]) -> id2
- Verify id1 == id2
- Verify only 1 file created
- Verify chunks_index has only 1 entry

#### test_add_chunk_with_project_id
- nm.add_chunk("test", ["#tag"], project_id="MyProject")
- Verify file in main_library/projects/MyProject/snippets/
- Verify project_id in index entry

#### test_add_chunk_with_agent_id
- nm.add_chunk("test", ["#tag"], agent_id="cline")
- Verify agent_id in index entry

#### test_add_chunk_with_source
- nm.add_chunk("test", ["#tag"], source="https://example.com")
- Verify source in file front-matter

#### test_search_by_keyword
- nm.add_chunk("hello world test", ["#tag"])
- results = nm.search("hello")
- Verify len(results) == 1

#### test_search_empty_index
- results = nm.search("anything")
- Verify results == []

#### test_search_filter_by_tags
- nm.add_chunk("test1", ["#a", "#b"])
- nm.add_chunk("test2", ["#c"])
- results = nm.search("test", tags=["#a"])
- Verify len(results) == 1
- Verify results[0]["tags"] contains "#a"

#### test_search_filter_by_project
- nm.add_chunk("test", ["#tag"], project_id="proj1")
- nm.add_chunk("test", ["#tag"], project_id="proj2")
- results = nm.search("test", project_id="proj1")
- Verify len(results) == 1

#### test_search_filter_by_agent
- nm.add_chunk("test", ["#tag"], agent_id="alice")
- nm.add_chunk("test", ["#tag"], agent_id="bob")
- results = nm.search("test", agent_id="alice")
- Verify len(results) == 1
- Verify only alice's chunk returned

#### test_search_no_match
- nm.add_chunk("hello world", ["#tag"])
- results = nm.search("xyznotfound")
- Verify results == []

#### test_archive_session_creates_file
- session_data = {"chat": ["hello"]}
- result = nm.archive_session("agent1", session_data)
- Verify JSON file exists in sessions/archived_sessions/agent_agent1/
- Verify file contains session_data

#### test_archive_session_updates_index
- Before: sessions_index["agent1"] not set
- nm.archive_session("agent1", {"data": 1})
- After: sessions_index["agent1"]["history"] has 1 entry with status "archived"

#### test_archive_session_unique_timestamp
- nm.archive_session("agent1", {"data": 1})
- nm.archive_session("agent1", {"data": 2})
- Verify 2 different files created (unique timestamp)

#### test_archive_session_empty_agent_id
- result = nm.archive_session("", {"data": 1})
- Verify raises or returns error

#### test_store_session_summary_creates_file
- summary = "# Summary\nTest content"
- result = nm.store_session_summary("agent1", summary)
- Verify .md file exists in sessions/session_summaries/agent_agent1/
- Verify file contains summary text

#### test_store_session_summary_updates_index
- nm.store_session_summary("agent1", "test summary")
- Verify sessions_index["agent1"]["last_session_summary"] points to file

#### test_store_session_summary_with_session_id
- result = nm.store_session_summary("agent1", "test", session_id="20260905_1200")
- Verify file named with session_id

#### test_get_context_returns_summary
- nm.store_session_summary("agent1", "# Test Summary\nDone: task1")
- context = nm.get_context_for_agent("agent1")
- Verify context contains "Test Summary"

#### test_get_context_empty_for_new_agent
- context = nm.get_context_for_agent("new_agent_xyz")
- Verify context == ""

#### test_get_context_with_project
- nm.add_joint_decision("proj1", {"decision": "use blake3", "by_agents": ["a", "b"], "reason": "fast"})
- nm.store_session_summary("agent1", "test")
- context = nm.get_context_for_agent("agent1", project_id="proj1")
- Verify context contains "use blake3"

#### test_get_context_fallback_collapsed
- Set sessions_index with collapsed_summary field
- Verify context reads collapsed file

#### test_add_joint_decision_creates_file
- nm.add_joint_decision("proj1", {"decision": "test", "by_agents": ["a"], "reason": "r"})
- Verify sessions/cross_sessions/collab_proj1/joint_decisions.json exists

#### test_add_joint_decision_appends
- nm.add_joint_decision("proj1", {"decision": "d1", "by_agents": ["a"], "reason": "r"})
- nm.add_joint_decision("proj1", {"decision": "d2", "by_agents": ["b"], "reason": "r"})
- Verify joint_decisions.json has 2 entries

## File: tests/test_summarizer.py

### Fixtures
```python
class TestSummarizer:
    @pytest.fixture
    def nm(self, nexus_store):
        from core.nexus_core import Nexus
        return Nexus(base_dir=nexus_store)
    
    @pytest.fixture
    def summarizer(self, nm):
        from core.summarizer import SessionSummarizer
        return SessionSummarizer(nm)
```

### Utility Tests

#### test_norm_key_basic
- from core.summarizer import _norm_key
- assert _norm_key("Some_Key") == "some_key"
- assert _norm_key("test key") == "test_key"

#### test_norm_key_cyrillic
- assert _norm_key("Тест_Ключ") == "тест_ключ"

#### test_truncate_short
- from core.summarizer import _truncate
- assert _truncate("hi") == "hi"

#### test_truncate_long
- result = _truncate("a" * 500)
- assert len(result) < 500
- assert result.endswith("…")

#### test_items_from_value_string
- from core.summarizer import _items_from_value
- assert _items_from_value("hello") == ["hello"]

#### test_items_from_value_list
- assert _items_from_value(["a", "b"]) == ["a", "b"]

#### test_items_from_value_dict_with_content
- assert _items_from_value({"content": "text"}) == ["text"]

#### test_items_from_value_dict_with_role
- result = _items_from_value({"role": "user", "content": "hi"})
- assert "[user] hi" in result

#### test_items_from_value_none
- assert _items_from_value(None) == []

### SessionSummarizer Tests

#### test_list_archives_empty
- archives = summarizer.list_archives("new_agent")
- assert archives == []

#### test_list_archives_sorted
- nm.archive_session("agent1", {"data": 1})
- nm.archive_session("agent1", {"data": 2})
- archives = summarizer.list_archives("agent1")
- Verify sorted by timestamp ascending

#### test_find_archive_newest
- nm.archive_session("agent1", {"data": 1})
- nm.archive_session("agent1", {"data": 2})
- path, ts = summarizer._find_archive_path("agent1")
- Verify path is the newest archive

#### test_find_archive_by_id
- nm.archive_session("agent1", {"data": 1})
- path, ts = summarizer._find_archive_path("agent1", session_id="...")
- Verify correct archive found

#### test_find_archive_not_found
- path, ts = summarizer._find_archive_path("nonexistent")
- assert path is None

#### test_extract_structure_empty
- buckets = summarizer.extract_structure({})
- assert all(v == [] for v in buckets.values())

#### test_extract_structure_actions
- buckets = summarizer.extract_structure({"actions": ["task1"]})
- assert "task1" in buckets["done"]

#### test_extract_structure_decisions
- buckets = summarizer.extract_structure({"decisions": ["use blake3"]})
- assert "use blake3" in buckets["decisions"]

#### test_extract_structure_next_steps
- buckets = summarizer.extract_structure({"next_steps": ["fix bug"]})
- assert "fix bug" in buckets["next"]

#### test_extract_structure_chat_history
- buckets = summarizer.extract_structure({"chat_history": [{"role": "user", "content": "hi"}]})
- assert len(buckets["dialog"]) > 0

#### test_extract_structure_unknown_key
- buckets = summarizer.extract_structure({"weird_key": "value"})
- assert "weird_key: value" in buckets["other"]

#### test_extract_structure_russian_keys
- buckets = summarizer.extract_structure({"сделано": ["task1"], "решения": ["d1"], "дальше": ["next"]})
- assert "task1" in buckets["done"]
- assert "d1" in buckets["decisions"]
- assert "next" in buckets["next"]

#### test_extract_structure_nested_dict
- data = {"actions": [{"content": "c1"}, {"text": "c2"}]}
- buckets = summarizer.extract_structure(data)
- assert len(buckets["done"]) >= 1

#### test_extract_structure_limits
- data = {"actions": [f"item{i}" for i in range(20)]}
- buckets = summarizer.extract_structure(data)
- assert len(buckets["done"]) <= 10  # MAX_ITEMS_PER_BUCKET

#### test_generate_session_summary_new_agent
- result = summarizer.generate_session_summary("nonexistent")
- assert result["status"] == "not_found"

#### test_generate_session_summary_existing
- nm.archive_session("agent1", {"actions": ["done task"], "decisions": ["use blake3"]})
- result = summarizer.generate_session_summary("agent1")
- assert result["status"] == "ok"
- assert result["summary_file"] is not None

#### test_generate_session_summary_marks_archived
- nm.archive_session("agent1", {"actions": ["t1"]})
- summarizer.generate_session_summary("agent1")
- archives = summarizer.list_archives("agent1")
- assert archives[-1]["status"] == "summarized"

#### test_generate_session_summary_preserves_pointer
- nm.archive_session("agent1", {"actions": ["t1"]})
- summarizer.generate_session_summary("agent1")  # creates summary for newest
- # archive another
- nm.archive_session("agent1", {"actions": ["t2"]})
- # generate for older session explicitly
- old_archive = summarizer.list_archives("agent1")[0]
- old_ts = old_archive["timestamp"]
- summarizer.generate_session_summary("agent1", session_id=old_ts)
- # last_session_summary should still point to the newer one
- context = nm.get_context_for_agent("agent1")
- assert "t2" in context

#### test_collapse_history_nothing_to_collapse
- nm.archive_session("agent1", {"actions": ["t1"]})
- result = summarizer.collapse_history("agent1", keep_last=1)
- assert result["status"] == "nothing_to_collapse"

#### test_collapse_history_collapses
- nm.archive_session("agent1", {"actions": ["t1"]})
- nm.archive_session("agent1", {"actions": ["t2"]})
- nm.archive_session("agent1", {"actions": ["t3"]})
- result = summarizer.collapse_history("agent1", keep_last=1)
- assert result["status"] == "ok"
- assert result["collapsed"] == 2

#### test_collapse_history_preserves_latest
- nm.archive_session("agent1", {"actions": ["t1"]})
- nm.archive_session("agent1", {"actions": ["t2"]})
- nm.archive_session("agent1", {"actions": ["t3"]})
- summarizer.collapse_history("agent1", keep_last=1)
- context = nm.get_context_for_agent("agent1")
- assert "t3" in context  # latest preserved

#### test_build_summary_markdown_empty
- md = summarizer._build_summary_markdown("agent1", "20260905_1200", {}, "session.json")
- assert "архив пуст" in md or "empty" in md.lower()

#### test_build_summary_markdown_with_data
- data = {"actions": ["done"], "decisions": ["used blake3"], "next_steps": ["fix bug"], "chat_history": [{"role": "user", "content": "hi"}]}
- md = summarizer._build_summary_markdown("agent1", "20260905_1200", data, "session.json")
- assert "Сделано" in md
- assert "Решения" in md
- assert "Дальше" in md
- assert "Диалог" in md

## File: tests/test_session_hook.py

### Fixtures
```python
class TestSessionHook:
    @pytest.fixture
    def nm(self, nexus_store):
        from core.nexus_core import Nexus
        return Nexus(base_dir=nexus_store)
    
    @pytest.fixture
    def hook(self, nm):
        from core.session_hook import SessionHook
        return SessionHook(nm)
```

#### test_end_session_empty_agent_id
- result = hook.end_session("", {"actions": []})
- assert result["status"] == "invalid_input"

#### test_end_session_empty_data
- result = hook.end_session("agent1", {})
- assert result["status"] == "invalid_input"

#### test_end_session_creates_archive
- result = hook.end_session("agent1", {"actions": ["task1"]})
- assert result["status"] == "ok"
- assert result["archive_file"] is not None

#### test_end_session_creates_summary
- result = hook.end_session("agent1", {"actions": ["task1"]})
- assert result["summary_file"] is not None
- assert result["summary_status"] == "ok"

#### test_end_session_returns_stats
- result = hook.end_session("agent1", {"actions": ["t1"], "decisions": ["d1"], "next_steps": ["n1"]})
- assert "stats" in result
- assert result["stats"]["done"] == 1
- assert result["stats"]["decisions"] == 1
- assert result["stats"]["next"] == 1

#### test_end_session_collapse_after_true
- nm.archive_session("agent1", {"actions": ["old1"]})
- nm.archive_session("agent1", {"actions": ["old2"]})
- result = hook.end_session("agent1", {"actions": ["new"]}, collapse_after=True)
- assert result["collapsed"] is not None

#### test_end_session_collapse_after_false
- result = hook.end_session("agent1", {"actions": ["t1"]}, collapse_after=False)
- assert result["collapsed"] is None

#### test_end_session_message_contains_paths
- result = hook.end_session("agent1", {"actions": ["t1"]})
- assert "archive" in result["message"].lower() or "архив" in result["message"].lower()
