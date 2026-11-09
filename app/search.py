"""Hybrid retrieval over knowledge/index.json: BM25 keywords + bge-small-zh vectors, fused with RRF.

    BM25    exact terms: "OpenVINO 绑核", "mFRR", "IEC 104", numbers
    vectors meaning: "你怎么解决高并发" finds "多路并发与性能" without sharing a word
    RRF     score = sum over both rankings of 1 / (60 + rank); no score scaling to tune

BM25 tokens: ASCII words (lower case) and, for Chinese, overlapping 2-character pieces, so
"告警录像" matches "录像" without a word segmenter. Heading words count three times.
Vectors are built offline (scripts/build_index.py -> knowledge/vectors.npz); the query is embedded
here with the same model (local ONNX, ~5 ms on CPU). Without the model or the vectors it is BM25 only.
"""
import json
import logging
import math
import re
from collections import Counter
from pathlib import Path

log = logging.getLogger("search")
K1, B = 1.4, 0.75
RRF_K = 60
POOL = 20               # candidates taken from each ranking before fusing
MIN_COS = 0.45          # vector hits below this are noise (bge-small-zh: unrelated text is ~0.2-0.4)
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："     # bge-zh query instruction
_ASCII = re.compile(r"[a-z0-9][a-z0-9_.+#\-]*")
_CJK = re.compile(r"[一-鿿]+")


def tokens(text):
    text = text.lower()
    out = [w.strip(".-") for w in _ASCII.findall(text)]
    for run in _CJK.findall(text):
        out += [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
    return [t for t in out if t]


class Embedder:
    """fastembed (ONNX Runtime) text embedding, loaded on first use from a local cache dir."""

    def __init__(self, model, cache_dir, threads=2):
        self.model, self.cache_dir, self.threads, self._m = model, str(cache_dir), threads, None

    def _load(self):
        if self._m is None:
            from fastembed import TextEmbedding
            self._m = TextEmbedding(self.model, cache_dir=self.cache_dir, threads=self.threads,
                                    local_files_only=True)
        return self._m

    def docs(self, texts):
        import numpy as np
        return np.array(list(self._load().embed(texts, batch_size=32)), dtype="float32")

    def query(self, text):
        import numpy as np
        return np.array(next(iter(self._load().embed([QUERY_PREFIX + text]))), dtype="float32")


class Index:
    def __init__(self, items, vectors=None, embedder=None):
        self.items = items
        self.docs = [Counter(tokens(it["title"]) * 3 + tokens(it["text"])) for it in items]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / max(len(self.lens), 1)
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.vectors, self.embedder = None, None
        if vectors is not None and embedder is not None and len(vectors) == len(items):
            import numpy as np
            self.vectors = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)
            self.embedder = embedder

    @property
    def mode(self):
        return "hybrid" if self.vectors is not None else "bm25"

    @classmethod
    def load(cls, path, vectors_path=None, embedder=None):
        p = Path(path)
        items = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        vectors = None
        vp = Path(vectors_path) if vectors_path else None
        if embedder and vp and vp.exists():
            import numpy as np
            z = np.load(vp)
            if str(z["model"]) == embedder.model:
                vectors = z["vectors"]
            else:
                log.warning("vectors built with %s, not %s: BM25 only", z["model"], embedder.model)
        return cls(items, vectors, embedder)

    def bm25(self, query, allowed):
        q = set(tokens(query))
        scored = []
        for i in allowed:
            d = self.docs[i]
            s = 0.0
            for t in q:
                f = d.get(t)
                if f:
                    s += self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * self.lens[i] / self.avg))
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        return scored[:POOL]

    def dense(self, query, allowed):
        import numpy as np
        if self.vectors is None or not allowed:
            return []
        try:
            q = self.embedder.query(query)
        except Exception:                       # model missing or broken: keyword search still works
            log.exception("query embedding failed")
            return []
        q /= max(float(np.linalg.norm(q)), 1e-9)
        idx = np.array(allowed)
        sims = self.vectors[idx] @ q
        order = np.argsort(-sims)[:POOL]
        return [(float(sims[j]), int(idx[j])) for j in order if sims[j] >= MIN_COS]

    def search(self, query, project=None, k=4):
        """Top k chunks; each hit has "via": {"bm25": rank, "vec": rank} (1-based, only the rankings it was in)."""
        allowed = [i for i, it in enumerate(self.items) if not project or it["project"] == project]
        fused, via = {}, {}
        for name, ranked in (("bm25", self.bm25(query, allowed)), ("vec", self.dense(query, allowed))):
            for r, (_, i) in enumerate(ranked, 1):
                fused[i] = fused.get(i, 0.0) + 1 / (RRF_K + r)
                via.setdefault(i, {})[name] = r
        best = sorted(fused, key=lambda i: -fused[i])[:k]
        return [{**self.items[i], "score": round(fused[i] * 1000, 2), "via": via[i]} for i in best]
