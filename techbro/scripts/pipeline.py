#!/usr/bin/env python3
"""
pipeline.py — Orchestrator: scrape → score → generate → stage in SQLite.
Run: python3 scripts/pipeline.py [--top N] [--dry-run]
"""
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scraper import scrape_all
from generator import generate_carousel
from db import get_db, upsert_article, stage_post, get_stats

TOP_N = 1  # articles per run

def _normalize_title(title: str) -> str:
    """Normalize title for dedup: lowercase, strip punctuation, collapse spaces."""
    import re
    t = re.sub(r'[^a-z0-9\s]', '', title.lower())
    return ' '.join(t.split())

def _title_overlap(t1: str, t2: str) -> float:
    """Jaccard similarity on word sets. >0.5 = likely same story."""
    w1 = set(_normalize_title(t1).split())
    w2 = set(_normalize_title(t2).split())
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / len(w1 | w2)

def run(top_n: int = TOP_N, dry_run: bool = False):
    t0 = time.time()

    # 1. Scrape + score
    print(f"[1/3] Scraping top {top_n} articles...")
    articles = scrape_all(top_n)
    if not articles:
        print("  No fresh articles found. Done.")
        return

    print(f"  Found {len(articles)} articles")
    conn = get_db()

    # Get already-posted/staged article titles for dedup
    posted_titles = [row['title'] for row in conn.execute(
        "SELECT a.title FROM posts p JOIN articles a ON p.article_id=a.id"
    ).fetchall()]

    for art in articles:
        print(f"\n  [{art['source']}] score={art['score']} | {art['title'][:60]}...")

        # Dedup: skip if already posted/staged (URL or title match)
        skip = False
        for pt in posted_titles:
            if _title_overlap(art['title'], pt) > 0.5:
                print(f"  [DEDUP] Similar to already-posted: {pt[:60]}...")
                skip = True
                break
        if skip:
            continue

        # 2. Upsert article to DB
        article_id = upsert_article(conn, art)
        print(f"  Article #{article_id} saved to DB")

        if dry_run:
            print("  [DRY RUN] Skipping generation")
            continue

        # 3. Generate carousel
        print(f"  [2/3] Generating carousel via LM...")
        slides = generate_carousel(art["title"], art["body"], art.get("image", ""), art.get("url", ""))
        if not slides:
            print("  ERROR: LM generation failed")
            continue

        provider = slides.pop("_provider", "unknown")
        print(f"  Generated via {provider}")

        # 4. Stage post
        post_id = stage_post(conn, article_id, slides, slides.get("caption", ""), slides.get("hashtags", ""))
        posted_titles.append(art['title'])  # add to dedup list for this batch
        print(f"  [3/3] Post #{post_id} staged in DB")

        # Preview
        print(f"  Hook: {slides.get('hook', '?')[:80]}")
        print(f"  CTA:  {slides.get('cta', '?')[:80]}")

    stats = get_stats(conn)
    elapsed = time.time() - t0
    print(f"\n{'='*50}")
    print(f"Done in {elapsed:.1f}s")
    print(f"DB: {stats['articles']} articles | {stats['staged']} staged | {stats['posted']} posted")
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=TOP_N)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.top, args.dry_run)
