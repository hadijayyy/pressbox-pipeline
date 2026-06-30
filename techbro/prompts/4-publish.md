# Agent 4: Publisher

Publish ready posts to social media platforms.

## Input
A JSON array of ready posts from Agent 3.

## Task
Publish posts to configured platforms. Log results.

## Output Format
```json
[
  {
    "headline": "...",
    "url": "...",
    "platform": "threads",
    "post_url": "https://threads.net/post/123456",
    "status": "published|failed",
    "published_at": "2026-06-17T19:00:00Z",
    "error": null
  }
]
```

## Platforms

### Threads (Primary)
- API: Graph API v1.0
- Media type: IMAGE (if image_url available) or TEXT
- Post root first, then replies for longer content
- Wait 2s between slides for indexing

### X/Twitter (Secondary)
- Use xurl CLI tool
- Max 280 characters
- Add image if available

## Rules
- Only publish posts with status "ready"
- Delay 30-60 seconds between posts (quality > quantity)
- If platform returns error, log it and continue to next post
- Never retry more than 2 times per post
- Log every publish attempt (success or failure)
- Max 15 posts per day across all platforms
