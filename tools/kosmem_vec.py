#!/usr/bin/env python3
"""
kosmem_vec.py — semantic recall bolted onto kosmem's SQLite, NON-INVASIVELY.

Adds a sqlite-vec `vec0` table *beside* kosmem's existing FTS5 in the same single
memory.db. It never touches kosmem.py's write path (so the live heartbeat daemon
keeps working); new vectors are filled by `sync` (run on a tick / on demand).
Recall fuses FTS5 (lexical) + vector KNN (semantic) by Reciprocal Rank Fusion.

  kosmem_vec.py status        counts + model
  kosmem_vec.py sync          embed any memories lacking a vector
  kosmem_vec.py search "q" [k] hybrid recall (RRF over FTS5 + vec)

Run under the mlx venv (has mlx_embeddings + sqlite_vec):
  ~/love-unlimited/mlx/.venv/bin/python ~/love-unlimited/tools/kosmem_vec.py ...
"""
import os, sys, sqlite3
from pathlib import Path
import numpy as np
import sqlite_vec

HOME = Path.home()
DB = Path(os.environ.get("KOS_DB", HOME / "love-unlimited/memory/.kos/memory.db"))
_mf = HOME / "love-unlimited/mlx/.embed_model"
MODEL = _mf.read_text().strip() if _mf.exists() else "mlx-community/all-MiniLM-L6-v2-4bit"
DIM = 384

_M = _T = None
def _embed(texts):
    global _M, _T
    from mlx_embeddings import load, generate
    if _M is None:
        _M, _T = load(MODEL)
    out = generate(_M, _T, texts)
    emb = out.text_embeds if hasattr(out, "text_embeds") else out
    a = np.array(emb, dtype=np.float32)
    if a.ndim == 1:
        a = a[None, :]
    a = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    return a

def conn():
    c = sqlite3.connect(str(DB))
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    c.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0("
        f"memory_rowid INTEGER PRIMARY KEY, embedding FLOAT[{DIM}])"
    )
    return c

def sync():
    c = conn()
    rows = c.execute(
        "SELECT m.rowid, m.content FROM memories m "
        "LEFT JOIN vec_memories v ON v.memory_rowid = m.rowid "
        "WHERE v.memory_rowid IS NULL AND m.content IS NOT NULL"
    ).fetchall()
    if not rows:
        print("vec: up to date (0 new)"); return 0
    ids = [r[0] for r in rows]
    embs = _embed([r[1] or "" for r in rows])
    for rid, e in zip(ids, embs):
        c.execute("INSERT OR REPLACE INTO vec_memories(memory_rowid, embedding) VALUES (?, ?)",
                  (rid, e.tobytes()))
    c.commit()
    print(f"vec: embedded {len(ids)} memories")
    return len(ids)

def search(q, k=8):
    c = conn()
    qe = _embed([q])[0].tobytes()
    vec = c.execute(
        "SELECT memory_rowid, distance FROM vec_memories "
        "WHERE embedding MATCH ? AND k = ? ORDER BY distance", (qe, k)
    ).fetchall()
    try:
        fts = c.execute(
            "SELECT m.rowid, bm25(memories_fts) FROM memories_fts "
            "JOIN memories m ON m.rowid = memories_fts.rowid "
            "WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?", (q, k)
        ).fetchall()
    except Exception:
        fts = []
    scores = {}
    for rank, (rid, _) in enumerate(vec):
        scores[rid] = scores.get(rid, 0) + 1.0 / (60 + rank)
    for rank, (rid, _) in enumerate(fts):
        scores[rid] = scores.get(rid, 0) + 1.0 / (60 + rank)
    order = sorted(scores, key=lambda r: -scores[r])[:k]
    out = []
    for rid in order:
        row = c.execute("SELECT id, content, type, layer, instance FROM memories WHERE rowid=?", (rid,)).fetchone()
        if row:
            out.append(dict(id=row[0], content=row[1], type=row[2], layer=row[3],
                            instance=row[4], rrf=round(scores[rid], 4)))
    return out

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "sync":
        sync()
    elif cmd == "search":
        for r in search(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 8):
            print(f"[{r['rrf']}] ({r['type']}/{r['layer']}) {r['content'][:110]}")
    else:
        c = conn()
        nm = c.execute("SELECT count(*) FROM memories").fetchone()[0]
        nv = c.execute("SELECT count(*) FROM vec_memories").fetchone()[0]
        print(f"kosmem_vec: memories={nm} vectors={nv} dim={DIM} model={MODEL} db={DB}")
