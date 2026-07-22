"""Pressbox Scoring Module — Football-specific additive 0-120 scoring.

Adapted from Market Monday v17 scoring system.
Architecture: 7 named components, each capped. Independently debuggable.

Components:
  1. Keyword Match   : +8 pts per unique include keyword (max 5 = 40 pts)
  2. Category Relev  : 20 (transfer/match/drama) / 10 (international) / 0 (none)
  3. Recency         : 15 (<6h) / 10 (6-24h) / 5 (24-48h) / 0 (>48h)
  4. Data/Konkret    : 15 (specific: score, fee, %) / 7 (vague digits) / 0
  5. Sumber Tier     : 10 (Tier 1) / 5 (Tier 2) / 0 (unknown)
  6. Audience Reach  : +10 per big team/nation/star mentioned (max 40)
  7. Drama Signal    : +5 per drama word in title (max 15)
  Dynamic Boost      : +15 proven hook (analytics), -20 worst topic (analytics)
  Penalti            : -1 hard reject if exclude keyword matched

Threshold: score >= 40 for pipeline (set in pressbox-mvp.py).
Big-audience topics (England, big clubs, drama) score 80-120 → auto-preferred.
"""

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


# ─── INCLUDE KEYWORDS ────────────────────────────────────────────────────────
# Football-specific. Case-insensitive. Short tokens (≤4 chars) get word-boundary.

INCLUDE_KEYWORDS = {
    # Transfer / Contract
    "transfer": [
        "transfer", "signing", "sign for", "signed for", "deal agreed",
        "fee confirmed", "contract extension", "new contract", "release clause",
        "buyout clause", "loan deal", "loan move", "permanent transfer",
        "transfer window", "transfer fee", "transfer target", "bid rejected",
        "bid accepted", "medical", "here we go", "official announcement",
        "pre-contract", "free agent", "swap deal", "player exchange",
        "add-ons", "installments", "wages", "salary", "weekly wage",
    ],
    # Match Results / Performance
    "match": [
        "hat-trick", "brace", "goal of the season", "last-minute goal",
        "injury time", "extra time", "penalty shootout", "adu penalti", "red card",
        "yellow card", "var decision", "offside goal", "disallowed goal",
        "own goal", "clean sheet", "man of the match", "match winner",
        "equalizer", "comeback win", "thrashing", "humiliated",
        "demolished", "destroyed", "battered", "upset", "giant-killing",
        "skor akhir", "juara", "menang", "kalah", "imbang",
    ],
    # Drama / Controversy
    "drama": [
        "outrage", "scandal", "banned", "suspended", "fined", "drama",
        "controversy", "furious", "slams", "hits out", "blasts",
        "row", "rift", "bust-up", "clash", "war of words",
        "refuses", "walks out", "storms off", "confrontation",
        "dressing room", "mutiny", "player revolt", "manager sack",
        "sacked", "resign", "quit", "stepping down", "under pressure",
    ],
    # World Cup / International
    "international": [
        "world cup", "fifa", "qualifier", "wc 2026", "usa 2026",
        "euros", "copa america", "nations league", "friendly international", "vs",
        "call-up", "squad announcement", "international break",
        "group stage", "knockout stage", "round of 16", "quarter-final", "kualifikasi",
        "semi-final", "final", "trophy", "champions league",
        "europa league", "conference league", "premier league",
        "la liga", "serie a", "bundesliga", "ligue 1",
        "piala dunia", "piala presiden", "liga 1", "liga indonesia",
        "liga inggris", "liga champion", "timnas",
    ],
    # Cross-cutting (global football, finance-related)
    "cross": [
        "financial fair play", "ffp", "psr", "profit sustainability",
        "relegation", "promotion", "playoff", "title race",
        "top four", "champions league spots", "tv deal",
        "broadcasting rights", "sponsorship", "kit deal",
        "stadium", "new ground", "expansion", "attendance record",
    ],
    # Human interest / Financial outrage / Real-world intersection
    # Proven 500K+ views: FIFA payout, visa denial, immigration blocks
    "human_interest": [
        "visa", "denied entry", "refused entry", "blocked entry",
        "banned from", "barred from", "turned away", "deport",
        "immigration", "border", "customs", "passport",
        "payout", "compensation", "insurance", "claim",
        "family", "mother", "father", "wife", "children",
        "tears", "cried", "emotional", "heartbreaking",
        "sacrifice", "suffering", "struggle", "ordeal",
        "fee", "cost", "price", "fine",
        "unfair", "injustice", "outrage", "disgrace",
        "human cost", "human toll", "price tag",
    ],
}


# ─── EXCLUDE KEYWORDS ────────────────────────────────────────────────────────
# Strict: substring match → hard reject (-1)

EXCLUDE_KEYWORDS = {
    "noise": [
        "prediksi zodiak", "ramalan", "gosip artis", "selebriti",
        "giveaway", "kuis berhadiah", "undian", "kontes foto",
        "tiktok viral", "instagram reel", "youtube shorts",
    ],
    "non_editorial": [
        "advertorial", "press release", "lowongan kerja",
        "event promosi", "sponsored content", "betting tips",
        "quiz",
        "odds", "accumulator", "bet of the day", "free bet",
        "casino", "slot online", "judi online",
    ],
    "commercial": [
        "snap up", "buy now", "shop now", "price drop",
        "coupon", "voucher", "bargain", "save £", "save $",
        "% off", "for £", "for $", "amazon", "ebay", "buy your",
        "add to basket", "checkout", "free delivery", "next day delivery",
        "black friday", "cyber monday", "prime day", "clearance",
    ],
    "gossip_roundup": [
        "monday's gossip", "tuesday's gossip", "wednesday's gossip",
        "thursday's gossip", "friday's gossip", "saturday's gossip",
        "sunday's gossip", "paper talk", "paper round",
        "transfer gossip", "rumour mill",
    ],
    "low_value": [
        # Referee / official info — proven <500 views, filler
        "who will referee", "who is the referee", "referee for", "referee appointed",
        "team news:", "predicted lineup", "predicted lineups",
        "predicted xi", "match preview", "preview:",
        # Generic logistics — no shares
        "what time is", "how to watch", "where to watch",
        "tv channel", "broadcast details", "streaming kick-off",
        # Quiz / trivia
        "can you name", "can you guess",
        # Future-telling
        "how far can", "where will they finish", "predicted finish",
    ],
    "non_football_context": [
        # Military / war — ALWAYS non-football, hard reject
        "soldiers died", "soldiers killed", "troops deployed",
        "falklands", "malvinas", "military coup", "war crimes",
        "prisoner of war", "casualties", "fallen soldiers",
        "armed forces", "navy", "air force", "regiment",
        "battle of", "invasion of",
        # Disaster / violence — hard reject
        "mass shooting", "terror attack", "hostage",
        "earthquake", "tsunami", "hurricane",
        "pandemic", "covid", "virus outbreak",
    ],
}


# ─── AMBIGUOUS EXCLUDES ──────────────────────────────────────────────────────
# Context-window check required (might be football OR non-football)
# Example: "liga" in "liga Indonesia" = football, "liga makan" = not
# Only flag if NO include keyword within ±200 chars

AMBIGUOUS_EXCLUDES = ["liga"]


# ─── SOURCE TIERS ────────────────────────────────────────────────────────────
# Football-specific sources

# Source: goal.com avg 58K views — 2.1x BBC (42K), mirror 27K
# So super tier gets +15, tier 1 = +10, tier 2 = +5
SOURCE_TIER_SUPER = [
]

SOURCE_TIER_1 = [
    "bbc sport", "sky sports", "the athletic", "guardian football",
    "espn fc", "football italia", "90min", "fabrizio romano",
    "transfermarkt", "goal.com", "goal",
]

SOURCE_TIER_2 = [
    "mirror", "sun", "daily mail", "express", "star",
    "football365", "talking points", "onefootball", "football london",
    "teamtalk", "hitc", "caughtoffside",
]


# ─── HELPER FUNCTIONS ────────────────────────────────────────────────────────

def compute_age_hours(pub_date_str):
    """Compute article age in hours from publish timestamp."""
    if not pub_date_str:
        return 999
    try:
        pub_date = parsedate_to_datetime(pub_date_str)
        now = datetime.now(timezone.utc)
        age = (now - pub_date).total_seconds() / 3600
        if age < 0:
            return 999  # Future-dated = invalid
        return age
    except Exception:
        return 999


def source_tier(source):
    """Return tier (0=super, 1, 2, 99=unknown) for source name."""
    s = (source or "").lower()
    for t in SOURCE_TIER_SUPER:
        if t in s:
            return 0
    for t in SOURCE_TIER_1:
        if t in s:
            return 1
    for t in SOURCE_TIER_2:
        if t in s:
            return 2
    return 99


# ─── AUDIENCE REACH BOOST ──────────────────────────────────────────────────
# Big teams/nations = massive built-in audience. +15-25 pts.
BIG_TEAMS = [
    "england", "brazil", "argentina", "germany", "france", "spain",
    "italy", "portugal", "netherlands", "belgium", "croatia",
    "manchester united", "man city", "manchester city", "liverpool",
    "arsenal", "chelsea", "tottenham", "real madrid", "barcelona",
    "bayern", "psg", "inter milan", "juventus", "ac milan",
    "atletico madrid", "napoli", "dortmund",
    "ronaldo", "messi", "mbappe", "haaland", "salah", "bellingham",
    "foden", "saka", "palmer", "yamal", "vinicius", "modric",
    "southgate", "tuchel", "guardiola", "klopp", "mourinho",
    "arteta", "slot", "ancelotti", "nagelsmann", "deschamps",
    "fifa", "uefa", "premier league", "champions league",
]
BIG_TEAMS_RE = [re.compile(r'\b' + re.escape(t) + r'\b') for t in BIG_TEAMS]

# High-engagement drama words in title (not body — title drives clicks)
DRAMA_WORDS = [
    "locked out", "fatal", "no way out",
    "row",
    "explosive", "shocking", "destroyed", "humiliated", "battered",
    "war of words", "bust-up", "rift", "scandal",
    "refuses", "walks out", "storms off",
    "collapsed", "disaster", "nightmare", "crisis",
    "fate confirmed", "forced", "denied", "disagrees", "rivals",
    "under fire", "disastrous", "catastrophic", "collapse",
    "betrayal", "backlash", "fury", "rowing", "tensions",
    "exclusive", "breaking", "drops bombshell",
    "responds", "admits", "sends message",
    "breaks silence", "sets record straight",
    "takes swipe", "calls out", "fires back", "double down",
    # Data-driven: 487K post pattern — accusation/controversy hooks
    "break own rule", "breaks own rule", "violation", "violated",
    "rigged", "fixing", "corruption", "corrupt", "cheating", "cheated",
    "unfair", "unfair advantage", "special treatment",
    "exemption", "secret deal", "backroom deal",
    "lies", "lying", "cover-up", "cover up", "conspiracy",
    "hypocrisy", "hypocrite", "double standard",
    "injustice", "disgrace", "shameful", "embarrassing",
    "clash", "standoff", "showdown", "confrontation",
    "angered", "incensed", "fuming", "livid",
    # Rule-break / governance outrage — proven viral territory
    "bends rules", "bending rules", "rule change", "loophole",
    "exposed", "caught out", "caught on camera", "leaked",
    "secret meeting", "backdoor", "cover-up", "officials",
    "banned from", "barred from", "kicked out", "expelled",
    "investigation", "investigates", "probe", "inquiry",
    "fifa ban", "uefa ban", "fifa rule", "uefa rule",
    "violates", "breach", "breaches", "breached",
    "told to", "ordered to", "forced to", "demands", "demanded",
    "under fire", "under scrutiny", "under review", "favouritism",
    "nepotism", "conflict of interest", "privilege", "elitist",
]


def check_include_keywords(text):
    """Returns (matched_count, categories_set). Case-insensitive.
    Short tokens (≤4 chars) use word-boundary regex to avoid substring false
    positives (e.g. 'cup' inside 'occupy', 'goal' inside 'goaltending').
    """
    text_lower = text.lower()
    matched = set()
    categories = set()
    for cat, keywords in INCLUDE_KEYWORDS.items():
        for kw in keywords:
            kw_lower = kw.lower()
            if len(kw_lower) <= 4:
                # Short token — require word boundary
                pattern = r"\b" + re.escape(kw_lower) + r"\b"
                if re.search(pattern, text_lower):
                    matched.add(kw)
                    categories.add(cat)
            else:
                if kw_lower in text_lower:
                    matched.add(kw)
                    categories.add(cat)
    return len(matched), categories


def check_exclude_keywords(text):
    """Check strict excludes + ambiguous excludes with context window.
    Returns matched exclude keyword (str) or None.
    """
    text_lower = text.lower()
    # Strict excludes — direct match
    for cat, keywords in EXCLUDE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                return kw
    # Ambiguous excludes — only flag if NO include keyword nearby (±100 chars).
    include_kws_flat = [kw.lower() for kws in INCLUDE_KEYWORDS.values() for kw in kws]
    context_window = 200
    for kw in AMBIGUOUS_EXCLUDES:
        if len(kw) <= 4:
            # Word-boundary match for short tokens
            pattern = r"\b" + re.escape(kw) + r"\b"
            match = re.search(pattern, text_lower)
            if not match:
                continue
            idx = match.start()
        else:
            idx = text_lower.find(kw)
            if idx == -1:
                continue
        context = text_lower[max(0, idx - context_window):idx + len(kw) + context_window]
        has_include_nearby = any(inc in context for inc in include_kws_flat)
        if not has_include_nearby:
            return f"{kw} (no football context)"
    return None


def has_specific_data(text):
    """Detect specific numbers (scores, fees, transfer amounts). Returns bool."""
    patterns = [
        r'\d+\s*-\s*\d+',                              # scores: 3-1, 2-0
        r'£\s*\d+[\d.,]*\s*(m|million|bn|billion)',    # GBP amounts
        r'€\s*\d+[\d.,]*\s*(m|million|bn|billion)',    # EUR amounts
        r'\$\s*\d+[\d.,]*\s*(m|million|bn|billion)',   # USD amounts
        r'\d+\.?\d*\s*(%|persen|percent)',              # percentages
        r'\d+\s*(poin|points|pts)',                     # points
        r'(premier league|la liga|serie a|bundesliga|ligue 1)\s*(table|standings)',
        r'(naik|turun|menang|kalah)\s*\d+',             # movement/result with numbers
        r'caps?\s*\d+',                                 # international caps
        r'goal(s)?\s*(in|from|during|scored)',          # goal counts
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


# ─── MAIN SCORING FUNCTION ──────────────────────────────────────────────────

def score_topic(t: dict) -> int:
    """Score article 0-100 for football content (Pressbox pipeline).

    Components:
      1. Keyword Match  : +8 pts per unique include keyword (max 5 = 40 pts)
      2. Category Relev : 20 (transfer/match/drama) / 10 (international) / 0 (none)
      3. Recency        : 15 (<6h) / 10 (6-24h) / 5 (24-48h) / 0 (>48h)
      4. Data/Konkret   : 15 (specific: score, fee, %) / 7 (vague digits) / 0
      5. Sumber Tier    : 10 (Tier 1) / 5 (Tier 2) / 0 (unknown)
      6. Audience Reach : +10 per big team/nation/star (max 40)
      7. Drama Signal   : +5 per drama word in title (max 15)
      8. First Ever     : +20 (first ever + stat) / +10 (first ever only)
      9. Niche Nation   : -15 (niche nation without big team in title)
     10. Paradox Bonus  : +12 (while/despite + counter-intuitive)
     11. Warning Bonus  : +8 ("you've been warned" / threat to big team)
      Penalti           : -1 hard reject if exclude keyword matched

    Returns:
      -1   → hard reject (posted URL or exclude match)
      0-100 → score (threshold ≥40 untuk pipeline)
    """
    title = t.get("title", "")
    desc = t.get("description", "")
    combined = f"{title} {desc}"

    # Hard reject: exclude keyword match
    exclude_kw = check_exclude_keywords(combined)
    if exclude_kw:
        return -1

    # 1. Keyword Match (max 40 pts)
    matched_count, categories = check_include_keywords(combined)
    keyword_pts = min(matched_count, 5) * 8

    # 2. Category Relevance (max 30 pts) — controversy/drama valued highest
    if categories & {"drama"}:
        cat_pts = 30
    elif categories & {"transfer", "match"}:
        cat_pts = 25
    elif categories & {"international", "cross"}:
        cat_pts = 10
    else:
        cat_pts = 0

    # 3. Recency (max 15 pts)
    age_h = compute_age_hours(t.get("published", ""))
    if age_h < 6:
        recency_pts = 15
    elif age_h < 24:
        recency_pts = 10
    elif age_h < 48:
        recency_pts = 5
    else:
        recency_pts = 0

    # 4. Data/Konkret (max 15 pts)
    if has_specific_data(combined):
        data_pts = 15
    elif re.search(r'\d+', combined):
        data_pts = 7
    else:
        data_pts = 0

    # 5. Sumber Kredibilitas (max 15 pts) — super tier (goal) = 15, tier 1 = 10, tier 2 = 5
    tier = source_tier(t.get("source", ""))
    if tier == 0:
        source_pts = 15
    elif tier == 1:
        source_pts = 10
    elif tier == 2:
        source_pts = 5
    else:
        source_pts = 0

    # 6. Audience Reach Boost (max 30 pts) — big teams/nations/players = massive audience
    audience_pts = 0
    title_lower = title.lower()
    combined_lower = combined.lower()
    matched_entities = set()
    for pat, name in zip(BIG_TEAMS_RE, BIG_TEAMS):
        if pat.search(combined_lower):
            matched_entities.add(name)
    audience_pts = min(len(matched_entities) * 10, 40)

    # 7. Drama/Engagement Signal in title (max 20 pts)
    drama_pts = 0
    for dw in DRAMA_WORDS:
        if dw in title_lower:
            drama_pts += 5
    drama_pts = min(drama_pts, 20)

    # 8. "First Ever" + Stat Boost (max 20 pts) — proven 75K views pattern
    # Matches: "first team", "first player", "first ever", "in history" + any number
    first_ever_pts = 0
    first_ever_patterns = [
        r'first\s+(?:team|player|manager|nation|club)',
        r'first\s+ever',
        r'in\s+(?:world\s+cup|football|tournament)\s+history',
    ]
    title_lower_combined = combined.lower()
    has_first_ever = any(re.search(p, title_lower_combined) for p in first_ever_patterns)
    has_number_in_title = bool(re.search(r'\d+', title))
    if has_first_ever and has_number_in_title:
        first_ever_pts = 20
    elif has_first_ever:
        first_ever_pts = 10

    # 9. Niche Nations Penalty (-15) — low reach, proven <200 views
    NICHE_NATIONS = [
        "hong kong", "dr congo", "congo", "madagascar", "comoros",
        "papua new guinea", "guam", "lesotho", "eritrea", "djibouti",
        "brunei", "laos", "cambodia", "myanmar", "bhutan", "maldives",
        "macau", "mongolia", "nepal", "sri lanka", "bangladesh",
    ]
    niche_pts = 0
    has_niche = any(n in title_lower for n in NICHE_NATIONS)
    has_big = any(pat.search(title_lower) for pat in BIG_TEAMS_RE)
    # Skip niche penalty if article has human_interest signals (visa, family, tears, etc.)
    _human_interest = ["visa", "denied entry", "refused entry", "family", "mother", "father",
                       "tears", "cried", "emotional", "heartbreaking", "sacrifice", "payout",
                       "compensation", "immigration", "unfair", "injustice", "disgrace",
                       "fee", "cost", "price tag", "human cost"]
    has_human = any(kw in title_lower for kw in _human_interest)
    if has_niche and not has_big and not has_human:
        niche_pts = -15

    # 10. Paradox Bonus (+12) — "while [counter-intuitive]" drives curiosity
    PARADOX_PATTERNS = [
        r'while\s+(?:barely|hardly|only|never|not|without)',
        r'despite\s+(?:only|never|not|barely|having)',
        r'without\s+(?:even|ever|a|the)',
        r'only\s+\d+\s*touch',  # "only 11 touches"
    ]
    paradox_pts = 0
    if any(re.search(p, title_lower_combined) for p in PARADOX_PATTERNS):
        paradox_pts = 12

    # 11. Warning/Threat Bonus (+8) — "[Big team], you've been warned" = debate bait
    WARNING_PATTERNS = [
        r"you'?ve\s+been\s+warned",
        r"watch\s+out",
        r"be\s+afraid",
        r"better\s+be\s+(?:worried|scared|ready)",
        r"they'?re\s+coming\s+for",
    ]
    warning_pts = 0
    if any(re.search(p, title_lower_combined) for p in WARNING_PATTERNS):
        warning_pts = 8

    # 12. Star Player Bonus (+30) — proven +39% above baseline, top 5 dominated by star names
    PROVEN_STARS = [
        r"\bhaaland\b", r"\bmbappe\b", r"\bmessi\b", r"\bkane\b",
        r"\bbellingham\b", r"\bsaka\b", r"\bsalah\b", r"\bvin[i]cius\b",
        r"\bde\s+bruyne\b", r"\brodri\b", r"\byamal\b", r"\bwirtz\b",
        r"\bmusiala\b", r"\bgavi\b", r"\bpedri\b", r"\bkvaratskhelia\b",
        r"\bosimhen\b", r"\bzirkzee\b", r"\bmainoo\b", r"\bpalmer\b",
        r"\bneymar\b", r"\blewandowski\b", r"\bhalland\b",
    ]
    star_player_pts = 0
    if any(re.search(p, title_lower) for p in PROVEN_STARS):
        star_player_pts = 30

    # 13. Conflict Hook Bonus (+10) — data: conflict avg 50,937v vs baseline 41,152v
    CONFLICT_KW = [
        r"\bvs\.?\b", r"\bagainst\b", r"\bclash\b", r"\brival\b", r"\bbattle\b",
        r"\bwar\s+of\s+words\b", r"\bstandoff\b", r"\bshowdown\b", r"\bdispute\b",
        r"\bfight\b", r"\bfeud\b", r"\bbust[-\s]up\b",
    ]
    conflict_hook_pts = 0
    if any(re.search(p, title_lower) for p in CONFLICT_KW):
        conflict_hook_pts = 10

    # 14. Timing Urgency Bonus (+8)
    TIMING_URGENCY = [
        r'\bjust\b', r'\bbreaking\b', r'\bminutes\s+ago\b',
        r'\blatest\b',
    ]
    urgency_pts = 0
    urgency_hits = sum(1 for p in TIMING_URGENCY if re.search(p, title_lower))
    if urgency_hits >= 1:
        urgency_pts = 8

    # 15. Human Story Bonus (+10) — proven highest engagement rate (1.5%)
    # "Haaland journey", "How Argentina got best out of Messi", "secret to"
    HUMAN_STORY_PATTERNS = [
        r"\bjourney\b", r"\bsecret\s+to\b",
        r"got\s+the\s+best\s+out\s+of",
        r"mates?,\s*mate",
        r"heart\s+of\b", r"\bstory\s+of\b",
        r"life\s+of\b", r"\bmissed\b",
        r"\bspecial\b", r"\bmeaning\b",
        r"what\s+(?:they\s+)?missed",
    ]
    human_story_pts = 0
    if any(re.search(p, title_lower) for p in HUMAN_STORY_PATTERNS):
        human_story_pts = 20

    # 16. Low Performer Penalty (-15) — factual/QA patterns proven <2K views
    LOW_PERFORMER_PATTERNS = [
        r"who\s+is\s+the\s+referee",
        r"do\s+players\s+miss\s+",
        r"rice\s+(?:fit|available)",
        r"will\s+(?:there|he|she|they)\s+",
        r"can\s+you\s+name",
        r"quiz:?\s+can",
        r"is\s+it\s+possible",
        r"how\s+to\s+watch",
        r"tv\s+channel",
        r"broadcast\s+(?:info|details)",
    ]
    low_performer_pts = 0
    if any(re.search(p, title_lower) for p in LOW_PERFORMER_PATTERNS):
        low_performer_pts = -15

    total = (
        keyword_pts + cat_pts + recency_pts + data_pts + source_pts +
        audience_pts + drama_pts + first_ever_pts + niche_pts + paradox_pts +
        warning_pts + star_player_pts + conflict_hook_pts + urgency_pts +
        human_story_pts + low_performer_pts
    )
    return total


