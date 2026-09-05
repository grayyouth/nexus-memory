# -*- coding: utf-8 -*-
"""
Verification for Stage 3: SessionSummarizer (generate_session_summary + collapse_history).

Runs against an ISOLATED sandbox store (temp dir), never touches nexus_store.
Prints PASS/FAIL per check and exits non-zero on failure.
"""
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from core.nexus_core import Nexus
from core.summarizer import SessionSummarizer

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="nexus_verify_") as tmp:
        store = Path(tmp) / "store"
        nm = Nexus(base_dir=store)
        s = SessionSummarizer(nm)

        # Isolation check: sandbox must not touch the real store
        real_store = Path(__file__).resolve().parent / "nexus_store"
        check("sandbox isolation", store.exists() and store != real_store)

        # --- Archive 3 rich sessions for agent "verifier" ---
        sessions = [
            {
                "chat_history": [
                    {"role": "user", "content": "Изучи пайплайн ингестии"},
                    {"role": "assistant", "content": "Прочитал core/ingestion.py, нашёл узкое место в чанковании"},
                ],
                "actions": [
                    "Прочитан core/ingestion.py (410 строк)",
                    "Протестирован run_ingestion на 5 файлах",
                ],
                "decisions": ["Чанк 1200 символов — оптимум для малых контекстов"],
                "next_steps": ["Добавить поддержку PDF"],
                "custom_metric": {"elapsed_sec": 42, "mode": "fast"},
            },
            {
                "actions": ["Написан тестовый скрипт для поиска"],
                "decisions": ["Поиск пока ключевой, семантика в Этапе 4"],
                "todo": ["Сравнить с BM25"],
            },
            {
                "actions": ["Исправлен баг чтения индекса"],
                "сделано": ["Обновлена документация README"],
                "решения": ["Формат сводки: Сделано/Решения/Дальше"],
                "дальше": ["Автогенерация сводок"],
            },
        ]
        for data in sessions:
            nm.archive_session("verifier", data)

        archives = s.list_archives("verifier")
        check("3 archives indexed", len(archives) == 3, f"got {len(archives)}")

        # --- generate_session_summary for the NEWEST archive ---
        info = s.generate_session_summary("verifier")
        check("generate status ok", info.get("status") == "ok", str(info.get("status")))
        summary_path = store / info["summary_file"]
        check("summary file exists", summary_path.exists())
        text = summary_path.read_text(encoding="utf-8")
        check("has 'Сделано' section", "## Сделано" in text)
        check("has 'Решения' section", "## Решения" in text)
        check("has 'Дальше' section", "## Дальше" in text)
        check("russian keys recognized", "Обновлена документация README" in text)
        check("stats present", info["stats"].get("done", 0) >= 1, str(info["stats"]))

        index = nm._read_json(nm.sessions_index_file)
        newest_entry = index["verifier"]["history"][-1]
        check("newest marked summarized", newest_entry.get("status") == "summarized")
        check(
            "pointer -> generated summary",
            index["verifier"]["last_session_summary"] == info["summary_file"],
        )

        # get_context serves the generated summary
        ctx = nm.get_context_for_agent("verifier")
        check("get_context serves summary", "Автосводка сессии" in ctx)

        # --- generate for a SPECIFIC session (oldest) ---
        oldest_ts = archives[0]["timestamp"]
        info_old = s.generate_session_summary("verifier", session_id=oldest_ts)
        old_text = (store / info_old["summary_file"]).read_text(encoding="utf-8")
        check("specific session summary", "Протестирован run_ingestion" in old_text)
        check("unknown key kept", "custom_metric: elapsed_sec: 42" in old_text)
        check(
            "backfill does not move latest pointer",
            nm._read_json(nm.sessions_index_file)["verifier"]["last_session_summary"]
            == info["summary_file"],
        )
        check(
            "unique archive files (no minute collisions)",
            len({e["file"] for e in s.list_archives("verifier")}) == 3,
        )


        # --- collapse_history: keep only the newest archive ---
        coll = s.collapse_history("verifier", keep_last=1)
        check("collapse ok", coll.get("status") == "ok", str(coll))
        check("collapsed 2 of 3", coll.get("collapsed") == 2, str(coll.get("collapsed")))
        collapsed_path = store / coll["summary_file"]
        check("collapsed file exists", collapsed_path.exists())
        ctext = collapsed_path.read_text(encoding="utf-8")
        check("collapsed lists old sessions", oldest_ts in ctext)
        check("aggregated decisions", "Чанк 1200 символов" in ctext)
        check("aggregated next", "Сравнить с BM25" in ctext)

        index = nm._read_json(nm.sessions_index_file)
        statuses = [e["status"] for e in index["verifier"]["history"]]
        check(
            "statuses updated",
            statuses.count("collapsed") == 2 and statuses[-1] == "summarized",
            str(statuses),
        )
        check(
            "pointer preserved (not replaced by collapse)",
            index["verifier"]["last_session_summary"] == info["summary_file"],
        )
        check("collapsed_summary recorded", "collapsed_summary" in index["verifier"])

        # get_context still serves the LATEST summary, not the collapsed one
        ctx2 = nm.get_context_for_agent("verifier")
        check("latest summary still served", "Автосводка сессии" in ctx2)

        # Fallback: fresh agent with only collapsed history gets context from it
        nm.archive_session("only_old", {"actions": ["была сессия"]})
        nm.archive_session("only_old", {"actions": ["ещё одна сессия"]})
        s.generate_session_summary("only_old")
        s.collapse_history("only_old", keep_last=0)
        idx_only = nm._read_json(nm.sessions_index_file)
        idx_only["only_old"]["last_session_summary"] = None
        nm._write_json(nm.sessions_index_file, idx_only)
        ctx3 = nm.get_context_for_agent("only_old")
        check("fallback to collapsed in get_context", "Свернутое резюме" in ctx3)

        # --- Edge cases ---
        nm.archive_session("empty_agent", {"q": 1})
        info_e = s.generate_session_summary("empty_agent")
        check("empty-ish archive handled", info_e.get("status") == "ok")
        etext = (store / info_e["summary_file"]).read_text(encoding="utf-8")
        check("other bucket catches junk", "q: 1" in etext)
        info_miss = s.generate_session_summary("ghost_agent")
        check("missing agent -> not_found", info_miss.get("status") == "not_found")

        # Core regression: paths derive from the instance base_dir
        check(
            "instance paths derive from base_dir",
            nm.sessions_index_file == store / "sessions" / "index.json",
        )
        nm.add_chunk("проверка путей", ["#test"], project_id="Sandbox")
        results = nm.search("проверка", tags=["#test"], project_id="Sandbox")
        check("add_chunk/search in sandbox", len(results) == 1)
        check(
            "chunk stored in sandbox (not real store)",
            (store / "main_library" / "projects" / "Sandbox" / "snippets").exists(),
        )

    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} failure(s): {FAILURES}")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
