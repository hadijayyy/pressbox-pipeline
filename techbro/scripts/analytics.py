#!/usr/bin/env python3
"""
analytics.py — fetch Threads insights for posted posts → store in performance table
Run: python3 analytics.py
"""
import json, os, sqlite3, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import httpx

DIR = Path(__file__).parent
DB  = DIR.parent / "data" / "pipeline.db"
WIB = timezone(timedelta(hours=7))
GRAPH = "https://graph.threads.net/v1.0"

def load_env():
    env = {}
    p = Path.home() / ".hermes" / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    os.environ.update(env)
    return env

def fetch_insights(threads_id: str, token: str) -> dict:
    r = httpx.get(
        f"{GRAPH}/{threads_id}/insights",
        params={
            "metric": "likes,replies,reposts,views",
            "access_token": token,
        },
        timeout=15,
    )
    if r.status_code != 200:
        return {}
    data = r.json().get("data", [])
    out = {}
    for item in data:
        out[item["name"]] = item.get("values", [{}])[-1].get("value", 0)
    return out

def main():
    env = load_env()
    token = os.environ.get("THREADS_TOKEN_RYANHADI", "")
    if not token:
        print("[ERR] THREADS_TOKEN_RYANHADI missing")
        sys.exit(1)

    if not DB.exists():
        print("[ERR] DB not found.")
        sys.exit(1)

    con = sqlite3.connect(DB)
    rows = con.execute("""
        SELECT p.id, p.threads_id FROM posts p
        WHERE p.threads_id IS NOT NULL AND p.threads_id != ''
        ORDER BY p.posted_at DESC
        LIMIT 50
    """).fetchall()

    if not rows:
        print("[ANALYTICS] No posted posts to check.")
        sys.exit(0)

    now = datetime.now(WIB).isoformat()
    updated = 0
    for post_id, threads_id in rows:
        ins = fetch_insights(threads_id, token)
        if not ins:
            continue
        con.execute("""
            INSERT INTO performance (post_id, likes, replies, reposts, views, fetched_at)
            VALUES (?,?,?,?,?,?)
        """, (
            post_id,
            ins.get("likes", 0), ins.get("replies", 0),
            ins.get("reposts", 0), ins.get("views", 0),
            now,
        ))
        updated += 1

    con.commit()
    print(f"[ANALYTICS] Updated {updated} posts.")

    # Print top performers
    top = con.execute("""
        SELECT a.title, a.source, p.likes+p.replies+p.reposts AS engagement, p.views
        FROM performance p
        JOIN posts po ON po.id = p.post_id
        JOIN articles a ON a.id = po.article_id
        ORDER BY engagement DESC
        LIMIT 5
    """).fetchall()
    if top:
        print("\n=== Top 5 performers ===")
        for t in top:
            print(f"  [{t[1]}] {t[0][:50]} — eng={t[2]} views={t[3]}")

if __name__ == "__main__":
    main()
