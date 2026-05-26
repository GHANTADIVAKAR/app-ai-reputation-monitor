# Social And Video Data Sources

This product should treat each platform as a source connector. The dashboard and backend are already shaped for this:

- web/news articles
- Instagram reels, posts, comments, stories where the account has legal access
- YouTube videos and Shorts
- X posts
- Reddit posts
- third-party social listening providers

## What Is Live Now

- Google News RSS: live public web/news mentions.
- Keyword expansion: generates names, aliases, hashtags, handles, topics, and search phrases for each target.
- YouTube Data API hook: searches videos/Shorts when `YOUTUBE_API_KEY` is set.
- X recent search hook: searches recent posts when `X_BEARER_TOKEN` is set.
- Reddit public search hook: enabled with `REDDIT_ENABLED=true`.
- Social provider webhook hook: accepts normalized mentions from tools like Brandwatch, Talkwalker, Sprinklr, Meltwater, Apify actors, or custom collectors.

## Video Watching Requirement

For videos, the system should prefer this order:

1. Use official transcript/caption text when available.
2. Use platform metadata: title, description, hashtags, creator, comments, engagement.
3. Use an approved video intelligence pipeline to transcribe audio and sample frames.
4. Send transcript and visual summary to AI for sentiment, claim extraction, and PR risk scoring.

The backend already stores:

- `mediaType`
- `platform`
- `video.watched`
- `video.analysisMethod`
- `video.note`

## Query-Based Social Listening

The system should not attempt uncontrolled scraping. Instead it builds a search map per target:

- exact names
- spelling variants
- initials
- likely handles
- likely hashtags
- movie/project terms
- risk/review terms
- user-provided query themes

Collectors use this search map to discover relevant public mentions across supported sources.

## Instagram Reality

Instagram stories are not generally public web data. Stories, comments, reels, and hashtag search require Meta-approved permissions, account access, or an approved data provider. The system should not scrape private stories or bypass platform restrictions.

Production options:

- Meta Graph API for accounts you own or are authorized to monitor.
- Instagram Basic Display for limited authorized account media.
- A social listening provider for public/relevant Instagram coverage.
- A custom compliant ingestion endpoint feeding `SOCIAL_LISTENING_WEBHOOK_URL`.

## X Reality

Use the X API for recent search, post metrics, and author metadata. High-volume monitoring usually requires a paid X API tier or a social listening vendor.

## Normalized Mention Shape

External providers can send:

```json
{
  "mentions": [
    {
      "title": "Short title",
      "url": "https://platform.example/post/123",
      "platform": "instagram",
      "source": "Instagram",
      "author": "@creator",
      "text": "Caption, transcript, or summary",
      "mediaType": "reel",
      "publishedAt": "2026-05-01T10:00:00.000Z",
      "engagement": 12000,
      "video": {
        "watched": true,
        "analysisMethod": "transcript+frames",
        "transcript": "..."
      }
    }
  ]
}
```
