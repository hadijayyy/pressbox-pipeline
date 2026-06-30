# Agent 1: Scraper

Scrape the latest football news from Sky Sports.

## Task
Fetch https://www.skysports.com/news and extract all article cards.

## Output Format
Return a JSON array:
```json
[
  {
    "headline": "Article headline text",
    "url": "https://www.skysports.com/full/article/url",
    "image_url": "https://e2.365dm.com/.../image.jpg",
    "category": "football|cricket|f1|etc"
  }
]
```

## Rules
- Only articles from the last 24 hours
- Skip duplicates (same URL)
- Get the real image from `data-src` attribute (NOT `src` which is a placeholder)
- Headline from `<a class="news-list__headline-link">`
- Category from URL path (e.g. /football/news/ = football)

## Source Selectors
```html
<div class="news-list__item">
  <a class="news-list__headline-link" href="URL">HEADLINE</a>
  <a class="news-list__figure">
    <img data-src="IMAGE_URL" />
  </a>
</div>
```
