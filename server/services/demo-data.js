const sampleSources = [
  {
    source: "YouTube",
    title: "Public reaction to the latest interview is divided",
    summary: "A popular discussion video says the actor gave a confident interview, but some comments felt the tone was arrogant.",
    sentiment: "mixed",
    url: "https://youtube.com/watch?v=demo-reaction"
  },
  {
    source: "News",
    title: "Fans praise recent charity support",
    summary: "A regional news story highlights a donation and community support activity that is receiving positive comments.",
    sentiment: "positive",
    url: "https://example.com/news/charity-support"
  },
  {
    source: "Reddit",
    title: "Audience debate around recent movie choices",
    summary: "A discussion thread questions recent script selection and says the next release needs stronger word of mouth.",
    sentiment: "negative",
    url: "https://reddit.com/r/tollywood/comments/demo"
  },
  {
    source: "Blog",
    title: "Why the upcoming release can still recover hype",
    summary: "A film blog argues that the trailer and music campaign can shift the current conversation in a positive direction.",
    sentiment: "positive",
    url: "https://example.com/blog/recover-hype"
  }
];

export function createDemoMentions(target) {
  return sampleSources.map((item, index) => ({
    id: `demo_${Date.now().toString(36)}_${index}`,
    targetId: target.id,
    title: `${target.name}: ${item.title}`,
    url: `${item.url}-${encodeURIComponent(target.name.toLowerCase())}`,
    source: item.source,
    author: item.source === "YouTube" ? "Demo Telugu Film Channel" : "Demo Source",
    publishedAt: new Date(Date.now() - index * 60 * 60 * 1000).toISOString(),
    engagement: 1200 + index * 875,
    rawText: `${item.title}. ${item.summary}`,
    discoveredAt: new Date().toISOString()
  }));
}
