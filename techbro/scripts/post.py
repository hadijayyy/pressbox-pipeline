#!/usr/bin/env python3
"""
post.py — pick oldest unposted staged post → post to Threads
Run: python3 post.py [--dry-run]
"""
import json, os, sqlite3, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # for threads_poster
from threads_poster import ThreadsPoster

DIR = Path(__file__).parent
DB  = DIR.parent / "data" / "pipeline.db"
WIB = timezone(timedelta(hours=7))
DRY = "--dry-run" in sys.argv

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

def main():
    env = load_env()
    token   = os.environ.get("THREADS_TOKEN_RYANHADI", "")
    user_id = os.environ.get("THREADS_USER_ID_RYANHADI", "")
    if not token or not user_id:
        print("[ERR] THREADS_TOKEN_RYANHADI or THREADS_USER_ID_RYANHADI missing in .env")
        sys.exit(1)

    if not DB.exists():
        print("[ERR] DB not found. Run pipeline.py first.")
        sys.exit(1)

    con = sqlite3.connect(DB)
    row = con.execute("""
        SELECT p.id, p.slides_json, p.image_url, a.title
        FROM posts p JOIN articles a ON a.id = p.article_id
        WHERE p.posted_at IS NULL
        ORDER BY p.staged_at ASC
        LIMIT 1
    """).fetchone()

    if not row:
        print("[POST] No staged posts. Exit.")
        sys.exit(0)

    post_id, slides_json, image_url, title = row
    slides = json.loads(slides_json)

    print(f"[POST] Posting post_id={post_id}: {title[:60]}")
    print(f"[POST] Image: {image_url}")
    for i, s in enumerate(slides, 1):
        print(f"  Slide {i} ({len(s)}c): {s[:80]}...")

    if DRY:
        print("[DRY] Skipping actual post.")
        sys.exit(0)

    poster = ThreadsPoster(access_token=token, user_id=user_id)
    # slide 1 gets the image, rest are text
    posts_text = slides[:]
    try:
        results = poster.post_thread(
            posts_text,
            image_url=image_url if image_url else None,
        )
    except TypeError:
        # fallback: post_thread may not accept image_url kwarg
        # attach image only to slide 1 by using reply chain manually
        results = poster.post_thread(posts_text)

    now = datetime.now(WIB).isoformat()
    first_id = results[0].post_id if results else ""
    con.execute(
        "UPDATE posts SET posted_at=?, threads_id=? WHERE id=?",
        (now, first_id, post_id),
    )
    con.commit()
    print(f"[POST] Done. threads_id={first_id}")

if __name__ == "__main__":
    main()
