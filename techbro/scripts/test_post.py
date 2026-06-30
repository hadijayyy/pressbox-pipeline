#!/usr/bin/env python3
"""Test: regenerate with improved prompt and stage for posting."""
import sys
sys.path.insert(0, "scripts")
from db import get_db, stage_post
from generator import generate_carousel

conn = get_db()
art = conn.execute("SELECT * FROM articles ORDER BY score DESC LIMIT 1").fetchone()
if not art:
    print("No articles in DB")
    sys.exit(1)

print(f"Article: {art['title']}")
print(f"Score: {art['score']}")

slides = generate_carousel(art["title"], art["body"], art["image"] or "", art["url"] or "")
if not slides:
    print("Generation failed")
    sys.exit(1)

provider = slides.pop("_provider", "unknown")
print(f"\nGenerated via {provider}")

for key in ["hook", "setup", "twist", "deep", "sowhat", "cta"]:
    print(f"\n=== {key.upper()} ===")
    print(slides.get(key, "(empty)"))

post_id = stage_post(conn, art["id"], slides, slides.get("caption", ""), slides.get("hashtags", ""))
print(f"\nStaged post #{post_id}")
conn.close()
