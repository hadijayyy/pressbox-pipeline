#!/usr/bin/env python3
"""Common utilities shared by Press Box pipeline modules.

Exports:
  HOME, SCRIPTS, STAGING, POSTED, WIB     — shared paths / timezone
  load_env()                                — load ~/.hermes/.env
  log(msg)                                  — timestamped stderr logger
  send_alert(subject, body)                 — stub for alerting
  STOPWORDS, REPLACEMENTS                  — text filtering data
  clean_words(text)                         — normalise → frozenset of tokens
  is_similar(new_title, posted_ws)          — Jaccard similarity check
  classify_topic_type(text)                 — topic categorisation
"""

import os, re, sys
from datetime import datetime, timezone, timedelta

# ── Paths ───────────────────────────────────────────────────────────
HOME = os.path.expanduser("~")
SCRIPTS = f"{HOME}/.hermes/scripts"
STAGING = f"{HOME}/.hermes/pressbox/staging.json"
POSTED = f"{HOME}/.hermes/pressbox/posted_topics.json"
WIB = timezone(timedelta(hours=7))


# ── Load env ────────────────────────────────────────────────────────
def load_env():
    """Load key=value pairs from ~/.hermes/.env (no subprocess)."""
    env = {}
    env_path = f"{HOME}/.hermes/.env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip("\"'")
    return env


# ── Logging ─────────────────────────────────────────────────────────
def log(msg):
    """Print a timestamped message to stderr."""
    ts = datetime.now(WIB).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True, file=sys.stderr)


def send_alert(subject, body):
    """Placeholder: alert / notify about pipeline events.

    Override with real notification logic (Slack, e-mail, etc.)
    without changing downstream call sites.
    """
    log(f"🔔 ALERT: {subject} — {body[:200]}")


# ── Text processing helpers ─────────────────────────────────────────
STOPWORDS = frozenset([
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "with", "by",
    "as", "is", "was", "are", "were", "be", "been", "has", "have", "had",
    "it", "its", "this", "that", "these", "those", "and", "or", "but",
    "not", "no", "if", "from", "up", "down", "out", "off", "over", "under",
    "about", "into", "than", "then", "also", "just", "will", "can", "all",
    "who", "what", "when", "where", "why", "how", "their", "his", "her",
    "our", "your", "my", "we", "he", "she", "they", "do", "does", "did",
])

REPLACEMENTS = {
    "manchester city": "man city",
    "manchester united": "man utd",
    "real madrid": "madrid",
    "barcelona": "barca",
    "tottenham": "spurs",
    "newcastle": "toon",
    "nottingham": "nottm",
    "wolverhampton": "wolves",
    "leicester": "foxes",
    "southampton": "saints",
    "west ham": "westham",
}


def clean_words(text):
    """Lowercase, normalise club names, strip punctuation → frozenset of significant words."""
    t = text.lower()
    for old, new in REPLACEMENTS.items():
        t = t.replace(old, new)
    t = re.sub(r"[^\w\s]", " ", t)
    words = t.split()
    return frozenset(w for w in words if w not in STOPWORDS and len(w) > 1)


def is_similar(new_title, posted_ws, threshold=0.35):
    """Jaccard-similarity check against a list of already-posted word-sets."""
    nw = clean_words(new_title)
    if not nw:
        return False
    for pw in posted_ws:
        if not pw:
            continue
        intersection = len(nw & pw)
        union = len(nw | pw)
        if union == 0:
            continue
        if intersection / union >= threshold:
            return True
    return False


# ── Topic classification ────────────────────────────────────────────
def classify_topic_type(text):
    """Classify a headline / topic string into a category.

    Mirrors the logic used by analytics-llm.py.
    """
    if not text:
        return "other"
    lower = text.lower()
    if any(w in lower for w in ["transfer", "signs", "signing", "move to", "bid", "contract"]):
        return "transfer_rumor"
    if any(w in lower for w in ["world cup", "wc", "2026", "tournament"]):
        if any(w in lower for w in ["guide", "preview", "squad", "team guide"]):
            return "WC_team_guide"
        return "tournament_news"
    if any(w in lower for w in ["controversy", "drama", "storms", "backlash", "fans react"]):
        return "controversy"
    if any(w in lower for w in ["analysis", "tactical", "formation", "system"]):
        return "tactical_analysis"
    if any(w in lower for w in ["profile", "career", "who is", "story of"]) or len(text.split()) < 30:
        return "player_profile"
    if any(w in lower for w in ["injury", "out for", "sidelined", "fitness"]):
        return "injury_update"
    return "other"
