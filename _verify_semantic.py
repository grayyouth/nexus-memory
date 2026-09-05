# -*- coding: utf-8 -*-
"""
Verification for Stage 4: SemanticSearch (semantic search, prompt block, cache).
Runs against an ISOLATED sandbox store (temp dir), never touches nexus_store.
"""
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from core.nexus_core import Nexus
from core.semantic import HashingEmbedder, SemanticSearch, _cosine

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


CHUNKS = [
    ("blake3 выбран для хэширования: в 3 раза быстрее SHA-256 и не требует OpenSSL. "
     "Идеально для хэширования паролей и больших файлов.", {"#hashing", "#decision"}, "Alpha"),
    ("ingestion pipeline превращает сырые документы в чанки по 1200 символов "
     "и индексирует их в библиотеке Nexus.", {"#ingestion"}, None),
    ("MCP сервер Nexus запускается по stdio, протокол JSON-RPC, "
     "инструменты регистрируются декоратором tool.", {"#mcp"}, None),
    ("Deploy with docker compose: build the image, run migrations, scale workers. "
     "Deployment uses env vars for secrets.", {"#deploy"}, "Beta"),
    ("Рецепт борща: свёкла, капуста, морковь, томатная паста. Варить 40 минут.",
     {"#cooking"}, None),
]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="nexus_semantic_") as tmp:
        store = Path(tmp) / "store"
        nm = Nexus(base_dir=store)
        sem = SemanticSearch(nm)

        # Isolation
        real_store = Path(__file__).resolve().parent / "nexus_store"
        check("sandbox isolation", store.exists() and store != real_store)

        # Determinism of the embedder across instances
        e1, e2 = HashingEmbedder(), HashingEmbedder()
        v1 = e1.embed("быстрое хэширование данных")
        v2 = e2.embed("быстрое хэширование данных")
        check("embedder deterministic", v1 == v2)
        check("vector normalized", abs(sum(x * x for x in v1) - 1.0) < 1e-6)
        check("vector dim", len(v1) == e1.dim)

        # Add chunks, build index
        for content, tags, project in CHUNKS:
            nm.add_chunk(content, list(tags), project_id=project)
        stats = sem.ensure_fresh()
        check("index built", stats["total"] == len(CHUNKS) and stats["added"] == len(CHUNKS),
              str(stats))

        # Persistence: vectors.json exists with meta
        check("vectors.json persisted", sem.index_file.exists())
        data = sem.index_file and nm._read_json(sem.index_file)
        check("index meta", data.get("embedder") == "hashing-v1" and data.get("dim") == e1.dim)

        # Semantic relevance (RU): hashing query -> blake3 chunk on top
        res = sem.search("быстрое хэширование паролей", top_k=3)
        top_body = ""
        if res:
            from core.semantic import _read_chunk_body
            top_body = _read_chunk_body(store / res[0]["source_file"])
        check("ru query finds blake3", "blake3" in top_body.lower(), res[0]["id"][:8] if res else "-")

        # Semantic relevance (EN): docker query -> deploy chunk on top
        res_en = sem.search("docker compose deployment", top_k=3)
        en_body = ""
        if res_en:
            from core.semantic import _read_chunk_body
            en_body = _read_chunk_body(store / res_en[0]["source_file"])
        check("en query finds docker", "docker compose" in en_body.lower(),
              res_en[0]["id"][:8] if res_en else "-")

        # Hybrid: literal phrase hit gets keyword bonus flag
        res_kw = sem.search("ingestion pipeline превращает", top_k=3)
        check("hybrid keyword_hit flag", bool(res_kw) and res_kw[0].get("keyword_hit") is True)

        # Filters: project scope respected
        res_p = sem.search("хэширование паролей", top_k=5, project_id="Alpha")
        check("project filter", bool(res_p) and all(
            (store / r["source_file"]) and r.get("project_id") == "Alpha" for r in res_p))

        # Incremental freshness: add 1 chunk -> only it gets embedded
        nm.add_chunk("kubernetes deployment: helm charts, rolling updates, "
                     "liveness probes for cluster ops.", ["#deploy"], project_id="Beta")
        stats2 = sem.ensure_fresh()
        check("incremental embed", stats2["added"] == 1 and stats2["removed"] == 0
              and stats2["total"] == len(CHUNKS) + 1, str(stats2))

        # Cache invalidation: same query returns the new chunk now
        res_before = sem.search("kubernetes cluster", top_k=2)
        # (search above ran AFTER ensure_fresh; force a stale-cache scenario)
        sem._cache.clear()
        res_empty = sem.search("kubernetes cluster", top_k=2)
        # re-add semantics: cache key includes index version, so no stale hits
        res_k = sem.search("kubernetes cluster", top_k=2)
        check("cache consistent", res_empty == res_k)
        check("new chunk findable", bool(res_k) and "kubernetes" in
              _body(store / res_k[0]["source_file"]).lower())

        # Zero-vector query (no known tokens) must not crash
        res_z = sem.search("!!!??? ...", top_k=3)
        check("zero-vector query safe", res_z == [])

        # Prompt block: budget + structure + project filter
        block = sem.build_prompt_block("хэширование паролей и скорость",
                                       project_id="Alpha", max_chunks=2, max_chars=900)
        check("prompt block within budget", block["chars"] <= 900, str(block["chars"]))
        check("prompt block numbered", "[1]" in block["text"])
        check("prompt block project tag", "проект: Alpha" in block["text"])
        check("prompt block relevant", "blake3" in block["text"].lower())
        check("prompt block ≤2 chunks", len(block["chunks"]) <= 2)

        # Empty result message
        block_empty = sem.build_prompt_block("zzz unknown topic qqq", max_chars=600)
        check("empty block message", "не найдено" in block_empty["text"])

        # Related: excludes the chunk itself
        cid = res[0]["id"] if res else None
        rel = sem.related(cid, top_k=3) if cid else []
        check("related excludes self", all(r["id"] != cid for r in rel))

        # Fresh instance reuses the persisted index (no re-embedding)
        sem2 = SemanticSearch(nm)
        stats3 = sem2.ensure_fresh()
        check("persisted index reused", stats3["added"] == 0 and stats3["removed"] == 0,
              str(stats3))
        res2 = sem2.search("быстрое хэширование паролей", top_k=3)
        check("fresh instance searches", bool(res2) and "blake3" in
              _body(store / res2[0]["source_file"]).lower())

        # Core regression: index file lives inside the sandbox
        check("index file in sandbox", str(sem.index_file).startswith(str(store)))

    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} failure(s): {FAILURES}")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


def _body(path: Path) -> str:
    from core.semantic import _read_chunk_body
    return _read_chunk_body(path)


if __name__ == "__main__":
    sys.exit(main())

