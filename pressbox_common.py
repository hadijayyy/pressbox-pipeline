#!/usr/local/bin/python3
"""Common utilities shared by Press Box pipeline modules.

Exports:
  HOME, SCRIPTS, STAGING, POSTED, WIB     — shared paths / timezone
  load_env()                                — load ~/.hermes/.env
  log(msg)                                  — timestamped stderr logger
  STOPWORDS, REPLACEMENTS                  — text filtering data
  clean_words(text)                         — normalise → frozenset of tokens
  is_similar(new_title, posted_ws)          — Jaccard similarity check
  classify_topic_type(text)                 — topic categorisation
"""

import os, re, sys
from datetime import datetime, timezone, timedelta

# ── Paths ───────────────────────────────────────────────────────────
HOME = os.path.expanduser("~")
REPO_DIR = "/home/ubuntu/pressbox-pipeline"  # fixed path for cron compatibility
SCRIPTS = REPO_DIR  # all scripts now live in the repo
STAGING = {
    "v2": f"{HOME}/.hermes/pressbox/staging.json",
    "v3": f"{HOME}/.hermes/pressbox/staging-v3.json"
}
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
def log(msg, component=None):
    """Print a timestamped message to stderr."""
    ts = datetime.now(WIB).strftime("%H:%M:%S")
    if component:
        print(f"[{ts}] [{component}] {msg}", flush=True, file=sys.stderr)
    else:
        print(f"[{ts}] {msg}", flush=True, file=sys.stderr)


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
_INJURY_KW  = {"injury", "injured", "sidelined", "fitness", "out for", "ruled out"}
_TRANSFER_KW = {"transfer", "signs", "signing", "sign", "move to", "bid", "contract",
                "offer", "fee", "€", "£", "million", "deal"}
_MANAGERIAL_KW = {"sacked", "fired", "appointed", "dismissed", "replaces",
                  "manager", "head coach", "coaching change", "new boss"}
_POLITICAL_KW = {"ban", "banned", "banne", "protest", "visa", "travel",
                 "trump", "government", "policy", "staff denied", "oppressed",
                 "u-turn", "backlash", "boo", "booed", "complaint", "fifa",
                 "iran", "political", "diplomat", "sanction", "restrict"}
_CONTROVERSY_KW = {"controversy", "scandal", "racism", "racist", "abuse",
                   "hate symbol", "var official"}
_TACTICAL_KW = {"tactical", "formation", "system", "analysis", "pressing",
                "var", "red card", "yellow card", "penalty", "penalties",
                "offside", "referee", "officials"}
_MATCH_KW = {"win", "wins", "beat", "defeat", "victory", "score", "goal",
             "result", "draw", "draws", "lost", "loses", "beat"}
_PROFILE_KW = {"profile", "career", "who is", "story of", "rise of", "biography"}


def classify_topic_type(text):
    """Classify a topic string into a category."""
    if not text:
        return "other"
    lower = text.lower()
    if any(w in lower for w in _INJURY_KW):
        return "injury_update"
    if any(w in lower for w in _TRANSFER_KW):
        return "transfer_rumor"
    if any(w in lower for w in _MANAGERIAL_KW):
        return "managerial_change"
    if any(w in lower for w in _POLITICAL_KW):
        return "fifa_political"
    if any(w in lower for w in _CONTROVERSY_KW):
        return "controversy"
    if any(w in lower for w in _TACTICAL_KW):
        return "tactical_analysis"
    if any(w in lower for w in _MATCH_KW):
        return "match_result"
    if any(w in lower for w in _PROFILE_KW):
        return "player_profile"
    return "other"
