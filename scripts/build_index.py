"""Build the knowledge base: knowledge/index.json (chunks) + knowledge/vectors.npz (embeddings).

    python scripts/build_index.py [--root /root/proj] [--no-vectors]

Sources:
  knowledge/projects/<id>.md   my write-up per project (architecture, problems, my part, numbers)
  <repo>/README.md, docs/*.md  each project's own docs (tracked in git only)
Each section (## / ### heading) becomes one chunk; doc chunks link to that heading on GitHub,
write-up chunks link to the repo. Long sections are split at paragraph breaks.
Vectors: bge-small-zh-v1.5 via fastembed, on "title + text". Run again after any doc changes.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

GH = "https://github.com/rfs151"
# repo dir -> project id (same ids as the homepage cards)
REPOS = {"ai-vision": "vision", "ai-qt": "qt", "ai-ems": "ems", "ai-cam": "cam",
         "ai-gvtest": "gvtest", "ai-medlens": "medlens", "ai-agent": "agent"}
MAX_CHARS = 1800
KNOW = Path(__file__).resolve().parent.parent / "knowledge"
OUT = KNOW / "index.json"
VEC = KNOW / "vectors.npz"


def anchor(title, seen):
    """GitHub's heading anchor: lower case, punctuation dropped, spaces -> '-', duplicates get -1, -2..."""
    a = re.sub(r"[^\w\- ]", "", title.strip().lower()).replace(" ", "-")
    n = seen.get(a, 0)
    seen[a] = n + 1
    return a if n == 0 else f"{a}-{n}"


def split_long(text):
    if len(text) <= MAX_CHARS:
        return [text]
    parts, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) > MAX_CHARS:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        parts.append(cur)
    return parts


def chunks_of(md, base_url, doc_title, anchors=True):
    seen, out = {}, []
    path = [doc_title]          # heading path: h1 > h2 > h3
    title, url, buf, in_code = doc_title, base_url, [], False

    def flush():
        text = "\n".join(buf).strip()
        if len(text) > 40:
            for i, part in enumerate(split_long(text)):
                out.append({"title": " > ".join(path), "url": url, "text": part, "part": i})

    for line in md.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        m = None if in_code else re.match(r"^(#{1,4})\s+(.+?)\s*#*$", line)
        if m:
            level, h = len(m.group(1)), m.group(2)
            a = anchor(h, seen)
            if level <= 3:
                flush()
                buf = []
                path = path[:max(level - 1, 1)] + [h] if level > 1 else [h]
                url = f"{base_url}#{a}" if anchors else base_url
                continue
        buf.append(line)
    flush()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/root/proj")
    ap.add_argument("--no-vectors", action="store_true")
    args = ap.parse_args()
    items = []
    ids = {v: k for k, v in REPOS.items()}
    for f in sorted((KNOW / "projects").glob("*.md")):
        pid = f.stem
        repo = ids.get(pid, pid)
        for c in chunks_of(f.read_text(encoding="utf-8"), f"{GH}/{repo}", f"{repo} 项目说明", anchors=False):
            items.append({"project": pid, "source": "writeup", **c})
    for repo, pid in REPOS.items():
        d = Path(args.root) / repo
        if not d.is_dir():
            continue
        files = subprocess.run(["git", "-C", str(d), "ls-files", "-z", "README.md", "docs/*.md"],
                               capture_output=True, text=True).stdout.split("\0")
        files = [f for f in files if f]
        for f in files:
            md = (d / f).read_text(encoding="utf-8", errors="replace")
            base = f"{GH}/{repo}" if f == "README.md" else f"{GH}/{repo}/blob/main/{f}"
            doc = repo if f == "README.md" else f"{repo}/{f}"
            for c in chunks_of(md, base, doc):
                items.append({"project": pid, "source": "docs", **c})
    OUT.write_text(json.dumps(items, ensure_ascii=False, indent=0), encoding="utf-8")
    by = {}
    for it in items:
        by[it["project"]] = by.get(it["project"], 0) + 1
    print(f"{len(items)} chunks -> {OUT}  {by}")
    if not args.no_vectors:
        import time
        import numpy as np
        from app.config import settings
        from app.search import Embedder
        t = time.time()
        e = Embedder(settings.embed_model, settings.model_dir, threads=None)
        vecs = e.docs([f"{it['title']}\n{it['text']}" for it in items])
        np.savez(VEC, vectors=vecs.astype("float32"), model=np.array(e.model))
        print(f"vectors {vecs.shape} in {time.time() - t:.1f}s -> {VEC}")


if __name__ == "__main__":
    main()
