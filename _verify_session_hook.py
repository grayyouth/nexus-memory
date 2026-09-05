# -*- coding: utf-8 -*-
"""
Verification for Stage 3 tail: SessionHook.end_session (one-call session save).

Runs against an ISOLATED sandbox store (temp dir), never touches nexus_store.
Prints PASS/FAIL per check and exits non-zero on failure.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from core.nexus_core import Nexus
from core.session_hook import SessionHook

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="nexus_hook_verify_") as tmp:
        store = Path(tmp) / "store"
        nm = Nexus(base_dir=store)
        hook = SessionHook(nm)

        # Isolation check: sandbox must not touch the real store
        real_store = Path(__file__).resolve().parent / "nexus_store"
        check("sandbox isolation", store.exists() and store != real_store)

        # --- Invalid input handling ---
        bad = hook.end_session("", {"actions": ["x"]})
        check("empty agent_id -> invalid_input", bad.get("status") == "invalid_input")
        bad2 = hook.end_session("verifier", {})
        check("empty session_data -> invalid_input", bad2.get("status") == "invalid_input")
        bad3 = hook.end_session("verifier", "   ")
        check("blank session_data -> invalid_input", bad3.get("status") == "invalid_input")

        # --- Basic end_session: archive + summary ---
        data1 = {
            "chat_history": [
                {"role": "user", "content": "Сделай X"},
                {"role": "assistant", "content": "Сделал X, проверил"},
            ],
            "actions": ["Реализован SessionHook.end_session"],
            "decisions": ["Один вызов вместо archive+summary"],
            "next_steps": ["Обновить документацию"],
        }
        info = hook.end_session("verifier", data1)
        check("end_session ok", info.get("status") == "ok", str(info.get("status")))
        check("archive_file reported", bool(info.get("archive_file")))
        check("summary_file reported", bool(info.get("summary_file")))

        archive_file = store / info["archive_file"]
        summary_file = store / info["summary_file"]
        check("archive exists", archive_file.exists())
        check("summary exists", summary_file.exists())

        with open(archive_file, "r", encoding="utf-8") as f:
            archived = json.load(f)
        check("session_data round-trip", archived.get("session_data") == data1)

        text = summary_file.read_text(encoding="utf-8")
        check("summary has 'Сделано'", "## Сделано" in text)
        check("summary has 'Решения'", "## Решения" in text)
        check("summary has 'Дальше'", "## Дальше" in text)
        check("summary mentions session content", "SessionHook" in text)
        check("stats present", info.get("stats", {}).get("done", 0) >= 1, str(info.get("stats")))

        index = nm._read_json(nm.sessions_index_file)
        check(
            "newest archive marked summarized",
            index["verifier"]["history"][-1].get("status") == "summarized",
        )

        ctx = nm.get_context_for_agent("verifier")
        check("get_context serves new summary", "Автосводка" in ctx and "SessionHook" in ctx)

        # --- Two saves within the same minute -> unique file names ---
        info2 = hook.end_session("verifier", {"actions": ["Вторая сессия в ту же минуту"]})
        check("second save ok", info2.get("status") == "ok")
        check("unique archive names", info2["archive_file"] != info["archive_file"])
        check("unique summary names", info2["summary_file"] != info["summary_file"])
        index = nm._read_json(nm.sessions_index_file)
        files = [e["file"] for e in index["verifier"]["history"]]
        check("both archives indexed", len(files) == 2, str(len(files)))
        check(
            "pointer moved to newest summary",
            index["verifier"]["last_session_summary"] == info2["summary_file"],
        )

        # --- collapse_after=True: fold old archives in the same call ---
        info3 = hook.end_session(
            "verifier",
            {"actions": ["Третья сессия"], "decisions": ["Решение третьей сессии"]},
            collapse_after=True,
            keep_last=1,
        )
        check("third save ok", info3.get("status") == "ok")
        coll = info3.get("collapsed") or {}
        check("collapse reported ok", coll.get("status") == "ok", str(coll.get("status")))
        check("collapsed 2 of 3", coll.get("collapsed") == 2, str(coll.get("collapsed")))
        check("collapsed file exists", (store / coll["summary_file"]).exists())

        index = nm._read_json(nm.sessions_index_file)
        statuses = [e["status"] for e in index["verifier"]["history"]]
        check(
            "statuses updated after collapse",
            statuses.count("collapsed") == 2 and statuses[-1] == "summarized",
            str(statuses),
        )
        check(
            "pointer is the latest summary",
            index["verifier"]["last_session_summary"] == info3["summary_file"],
        )
        ctx3 = nm.get_context_for_agent("verifier")
        check("latest summary still served after collapse", "Третья" in ctx3)

        # --- Russian keys and agent ids ---
        info_ru = hook.end_session(
            "русский_агент",
            {
                "сделано": ["Проверка русских ключей"],
                "решения": ["Русские ключи поддерживаются"],
                "дальше": ["Продолжить работу"],
            },
        )
        check("russian agent_id ok", info_ru.get("status") == "ok")
        ru_text = (store / info_ru["summary_file"]).read_text(encoding="utf-8")
        check("russian keys extracted", "Проверка русских ключей" in ru_text)

        # --- Layout / core regressions ---
        check(
            "archives under agent_<id>",
            (store / "sessions" / "archived_sessions" / "agent_verifier").exists(),
        )
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
