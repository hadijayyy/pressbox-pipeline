# Agent 2: Enricher

Enrich scraped news articles with additional context and engagement data.

## Input
A JSON array of articles from Agent 1 (scraper).

## Task
For each article, add:
1. **Summary** — 1-2 sentence summary of the article
2. **Key people** — Player/manager names mentioned
3. **Sentiment** — positive / negative / neutral
4. **Engagement score** — 1-10 based on controversy/drama/newsworthiness
5. **Topics** — relevant tags (e.g. transfer, match, injury, opinion)

## Output Format
```json
[
  {
    "headline": "...",
    "url": "...",
    "image_url": "...",
    "category": "...",
    "summary": "1-2 sentence summary",
    "key_people": ["Player Name", "Manager Name"],
    "sentiment": "positive|negative|neutral",
    "engagement_score": 8,
    "topics": ["transfer", "drama"]
  }
]
```

## Rules
- Keep original fields from Agent 1
- Engagement score: 10 = biggest controversy/drama, 1 = routine news
- Only include people with recognizable names (skip "a source")
- If you can't determine sentiment, default to "neutral"
- Topics max 3 tags per article

## Scoring Guide
| Score | Meaning |
|-------|---------|
| 9-10 | Major controversy, breaking news, emotional story |
| 7-8 | Interesting angle, debate-worthy, strong opinions |
| 5-6 | Solid news, notable but not explosive |
| 3-4 | Routine update, expected outcome |
| 1-2 | Minor detail, filler content |
