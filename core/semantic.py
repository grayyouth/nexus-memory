"""
Nexus Semantic Search (Stage 4 of the roadmap).

Semantic search over the Nexus library without heavy dependencies:

- `HashingEmbedder` — a deterministic feature-hashing embedder (unigrams +
  bigrams -> fixed-size L2-normalized vector). Pure Python, no downloads,
  works for Russian and English text. Optional `SentenceTransformerEmbedder`
  is used automatically if `sentence_transformers` is installed.
- `SemanticSearch` — persistent vector index over library chunks
  (`main_library/_index/vectors.json`), incremental freshness updates,
  hybrid scoring (cosine + keyword hit bonus), an in-process result cache
  invalidated by index version, and `build_prompt_block()` — a compact,
  copy-ready context block ("prompt injection") assembled from the top
  chunks under a character budget (context compression for small-context
  local LLMs).

No third-party packages are required; everything is stdlib.
"""

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.nexus_core import Nexus
from core.config import config

# --- Tunables (overridable via nexus_config.json → semantic.*) ---
DEFAULT_DIM = config.semantic.get("vector_size", 512)
KEYWORD_BONUS = 0.25
MAX_CACHE_ENTRIES = config.semantic.get("cache_size", 128)
ROUND_DIGITS = 6
_USE_SENTENCE_TRANSFORMERS = config.semantic.get("use_sentence_transformers", False)

_TOKEN_RE = re.compile(r"[а-яёa-z0-9]+")


def _tokenize(text: str) -> List[str]:
    """Lowercase word tokens; Cyrillic and Latin are both supported."""
    return _TOKEN_RE.findall(str(text).lower())


def _features(tokens: List[str]) -> List[str]:
    """Unigrams + adjacent bigrams (bigrams help phrase matching)."""
    feats = list(tokens)
    feats.extend(f"{a}_{b}" for a, b in zip(tokens, tokens[1:]))
    return feats


class HashingEmbedder:
    """
    Deterministic hashing-trick embedder.

    Each token feature is mapped to a (index, sign) pair via md5, so the same
    text always yields the same vector across processes and machines (unlike
    Python's built-in hash(), which is salted per process).
    """

    NAME = "hashing-v1"

    def __init__(self, dim: int = DEFAULT_DIM):
        self.dim = dim

    def _hash_feature(self, feature: str) -> Tuple[int, int]:
        digest = hashlib.md5(feature.encode("utf-8")).hexdigest()
        idx = int(digest[:8], 16) % self.dim
        sign = 1 if int(digest[8:16], 16) % 2 == 0 else -1
        return idx, sign

    def embed(self, text: str) -> List[float]:
        """Embed text into an L2-normalized vector of size self.dim."""
        counts: Dict[str, int] = {}
        for feature in _features(_tokenize(text)):
            counts[feature] = counts.get(feature, 0) + 1

        vec = [0.0] * self.dim
        for feature, tf in counts.items():
            idx, sign = self._hash_feature(feature)
            vec[idx] += sign * (1.0 + math.log(tf))

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0.0:
            vec = [v / norm for v in vec]
        return vec

    def is_zero(self, vec: List[float]) -> bool:
        return not any(vec)


def _cosine(a: List[float], b: List[float]) -> float:
    """Dot product of two L2-normalized vectors (cheap cosine)."""
    if len(a) != len(b) or not a:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _read_chunk_body(path: Path) -> str:
    """Read a snippet file and strip its YAML front matter."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return ""
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        return (parts[2] if len(parts) >= 3 else raw).strip()
    return raw.strip()


def _try_sentence_transformer():
    """Return a SentenceTransformer embedder if the library is installed AND config allows it."""
    if not _USE_SENTENCE_TRANSFORMERS:
        return None
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")

        class _STEmbedder:
            NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

            def __init__(self, model):
                self._model = model
                self.dim = model.get_sentence_embedding_dimension()

            def embed(self, text: str) -> List[float]:
                vec = self._model.encode(text or "", normalize_embeddings=True)
                return [float(x) for x in vec]

            def is_zero(self, vec: List[float]) -> bool:
                return not any(vec)

        return _STEmbedder(model)
    except Exception:
        return None


class SemanticSearch:
    """Semantic (vector) search over the Nexus library with a persistent index."""

    def __init__(self, nexus: Nexus, dim: int = DEFAULT_DIM):
        self.nm = nexus
        self.index_file = nexus.index_dir / "vectors.json"
        # Use the neural embedder when available, else the hashing embedder.
        self.embedder = _try_sentence_transformer() or HashingEmbedder(dim)
        self._cache: Dict[Tuple, List[Dict]] = {}

    # --- Index management ---

    def _load_index(self) -> Dict[str, Any]:
        data = self.nm._read_json(self.index_file)
        if not isinstance(data, dict):
            data = {}
        if (
            data.get("embedder") != self.embedder.NAME
            or data.get("dim") != self.embedder.dim
            or not isinstance(data.get("vectors"), dict)
        ):
            data = {"version": 1, "embedder": self.embedder.NAME,
                    "dim": self.embedder.dim, "vectors": {}}
        return data

    def _version_hash(self, entries: List[Dict]) -> str:
        ids = ",".join(sorted(str(e.get("id")) for e in entries))
        return hashlib.md5(ids.encode("utf-8")).hexdigest()[:12]

    def ensure_fresh(self) -> Dict[str, Any]:
        """
        Bring the vector index in sync with chunks_index.json.
        Embeds only new chunks, drops removed ones, rebuilds on embedder change.
        Returns stats: {total, added, removed, rebuilt}.
        """
        entries = self.nm._read_json(self.nm.chunks_index_file) or []
        data = self._load_index()
        vectors: Dict[str, List[float]] = data["vectors"]

        current_ids = {str(e["id"]) for e in entries}
        stale = [cid for cid in vectors if cid not in current_ids]
        for cid in stale:
            del vectors[cid]

        path_by_id = {str(e["id"]): e.get("source_file") for e in entries}
        added = 0
        for cid in current_ids:
            if cid in vectors:
                continue
            rel = path_by_id.get(cid)
            body = _read_chunk_body(self.nm.base_dir / rel) if rel else ""
            vec = self.embedder.embed(body)
            if not self.embedder.is_zero(vec):
                vectors[cid] = [round(v, ROUND_DIGITS) for v in vec]
                added += 1

        stats = {"total": len(vectors), "added": added,
                 "removed": len(stale), "rebuilt": False}
        if added or stale or data.get("version") != 1:
            self.nm._write_json(self.index_file, {
                "version": 1, "embedder": self.embedder.NAME,
                "dim": self.embedder.dim, "vectors": vectors,
            })
        return stats

    # --- Filtering (same semantics as Nexus.search) ---

    def _filtered_entries(
        self,
        tags: Optional[List[str]] = None,
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> List[Dict]:
        entries = self.nm._read_json(self.nm.chunks_index_file) or []
        out = []
        for entry in entries:
            if tags and not all(t in entry.get("tags", []) for t in tags):
                continue
            if project_id and entry.get("project_id") != project_id:
                continue
            if agent_id and entry.get("agent_id") != agent_id and not entry.get("project_id"):
                continue
            out.append(entry)
        return out

    # --- Search ---

    def _cache_key(self, query: str, tags, project_id, agent_id, top_k: int, version: str) -> Tuple:
        return (
            query.strip().lower(),
            tuple(sorted(tags or [])), project_id or "", agent_id or "",
            int(top_k), version,
        )

    def search(
        self,
        query: str,
        top_k: int = 5,
        tags: Optional[List[str]] = None,
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        hybrid: bool = True,
    ) -> List[Dict]:
        """
        Rank chunks by cosine similarity to the query; with hybrid=True a
        literal keyword hit adds KEYWORD_BONUS to the score. Results are
        cached per index version.
        """
        entries = self._filtered_entries(tags, project_id, agent_id)
        version = self._version_hash(entries)
        key = self._cache_key(query, tags, project_id, agent_id, top_k, version)
        if key in self._cache:
            return [dict(r) for r in self._cache[key]]

        self.ensure_fresh()
        vectors = self._load_index()["vectors"]

        qvec = self.embedder.embed(query)
        scored: List[Dict] = []
        if not self.embedder.is_zero(qvec):
            for entry in entries:
                cid = str(entry["id"])
                cvec = vectors.get(cid)
                if not cvec:
                    continue
                score = _cosine(qvec, cvec)
                if score > 0.0:
                    scored.append({**entry, "score": round(score, 4), "keyword_hit": False})

        scored.sort(key=lambda r: r["score"], reverse=True)

        if hybrid and scored:
            query_lower = query.strip().lower()
            for res in scored[: top_k * 4]:
                body = _read_chunk_body(self.nm.base_dir / res["source_file"]).lower()
                if query_lower and query_lower in body:
                    res["keyword_hit"] = True
                    res["score"] = round(res["score"] + KEYWORD_BONUS, 4)
            scored.sort(key=lambda r: r["score"], reverse=True)

        results = scored[: max(1, int(top_k))]
        self._cache[key] = [dict(r) for r in results]
        if len(self._cache) > MAX_CACHE_ENTRIES:
            self._cache.pop(next(iter(self._cache)))
        return [dict(r) for r in results]

    # --- Prompt block (injection + compression for small-context LLMs) ---

    def build_prompt_block(
        self,
        query: str,
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        max_chunks: int = 5,
        max_chars: int = 1800,
    ) -> Dict[str, Any]:
        """
        Assemble a compact, copy-ready context block for a local LLM:
        top chunks relevant to the query, trimmed to fit max_chars in total.
        This is the "prompt injection" (ready context block) plus "context
        compression" (top-K chunks instead of 'read everything').
        """
        results = self.search(
            query, top_k=max_chunks, tags=tags, project_id=project_id, agent_id=agent_id
        )
        header = (
            f"### Контекст из Nexus по запросу: «{query.strip()}»"
            + (f" (проект: {project_id})" if project_id else "")
            + "\n"
        )
        if not results:
            text = header + "\n(Релевантных заметок в памяти Nexus не найдено.)\n"
            return {"text": text[:max_chars], "chunks": [], "chars": len(text)}

        budget = max(200, int(max_chars) - len(header))
        per_chunk = budget // len(results)

        lines = [header]
        used_chunks = []
        for i, res in enumerate(results, 1):
            body = _read_chunk_body(self.nm.base_dir / res["source_file"])
            entry = (
                f"[{i}] score={res['score']:.3f}"
                f" | project: {res.get('project_id') or 'general'}"
                f" | tags: {', '.join(res.get('tags', [])) or '-'}\n"
            )
            room = per_chunk - len(entry) - 1
            if room <= 0:
                continue
            lines.append(entry + body[:room] + "\n")
            used_chunks.append({"id": res["id"], "score": res["score"],
                                "project_id": res.get("project_id"),
                                "source_file": res.get("source_file")})

        text = "\n".join(lines).strip()
        if len(text) > max_chars:  # hard guarantee
            text = text[:max_chars].rstrip() + "…"
        return {"text": text, "chunks": used_chunks, "chars": len(text)}

    # --- Related chunks ---

    def related(self, chunk_id: str, top_k: int = 5) -> List[Dict]:
        """Chunks most similar to the given chunk (itself excluded)."""
        self.ensure_fresh()
        vectors = self._load_index()["vectors"]
        base = vectors.get(str(chunk_id))
        if not base:
            return []
        entries = self.nm._read_json(self.nm.chunks_index_file) or []
        scored = []
        for entry in entries:
            cid = str(entry["id"])
            if cid == str(chunk_id):
                continue
            cvec = vectors.get(cid)
            if not cvec:
                continue
            score = _cosine(base, cvec)
            if score > 0.0:
                scored.append({**entry, "score": round(score, 4)})
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[: max(1, int(top_k))]
