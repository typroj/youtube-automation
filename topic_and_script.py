"""
TOPIC RESEARCHER + SCRIPT WRITER — Faceless YouTube Automation
================================================================
Two modules in one file:

1. TopicResearcher — Discovers trending topics from 5+ sources:
    Google Trends, YouTube Trending, Reddit, NewsAPI, YouTube Search
    Scores and ranks topics by virality, competition, and niche fit.

2. ScriptWriter — Generates retention-optimised YouTube scripts:
    Hook → pattern interrupts → CTA → scene-by-scene visual cues
    Outputs structured JSON with scenes for the video assembler.

Dependencies:
    pip install pytrends praw requests anthropic openai google-api-python-client
    pip install python-dotenv feedparser beautifulsoup4

Usage:
    from topic_and_script import TopicResearcher, ScriptWriter

    researcher = TopicResearcher(niche="ai_tools")
    topics = researcher.discover(count=5)

    writer = ScriptWriter(provider="anthropic")
    script = writer.generate(topic=topics[0], video_length=10)

    # script.scenes → feed directly into VideoAssembler
"""

import os
import re
import json
import time
import random
import hashlib
import logging
import sqlite3
import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod

# ═══════════════════════════════════════════════════════════════
#  PART 1: DATA MODELS
# ═══════════════════════════════════════════════════════════════

@dataclass
class Topic:
    """A discovered topic candidate for video creation."""
    title: str
    source: str                          # "google_trends" / "reddit" / "youtube" / "news"
    score: float = 0.0                   # Composite score 0–100
    search_volume_trend: str = ""        # "rising" / "breakout" / "stable"
    keyword: str = ""                    # Primary search keyword
    related_keywords: List[str] = field(default_factory=list)
    source_url: str = ""
    description: str = ""
    timestamp: str = ""
    competition_level: str = "unknown"   # "low" / "medium" / "high"
    niche_relevance: float = 0.0         # 0–1 how well it fits your niche
    content_angle: str = ""              # LLM-suggested unique angle
    fingerprint: str = ""                # Hash for deduplication

    def __post_init__(self):
        if not self.fingerprint:
            raw = f"{self.title.lower().strip()}{self.keyword.lower().strip()}"
            self.fingerprint = hashlib.md5(raw.encode()).hexdigest()[:12]
        if not self.timestamp:
            self.timestamp = datetime.datetime.utcnow().isoformat()


@dataclass
class ScriptScene:
    """A single scene in a video script."""
    scene_id: int
    narration: str                       # TTS text for this scene
    visual_cue: str                      # Description for image/video generation
    duration_estimate: float = 0.0       # Seconds (estimated from word count)
    scene_type: str = "content"          # "hook" / "content" / "pattern_interrupt" / "cta" / "outro"
    on_screen_text: str = ""             # Key text to overlay on screen
    transition: str = "cut"              # "cut" / "fade" / "zoom"


@dataclass
class Script:
    """A complete video script with all scenes."""
    topic: str
    title: str
    scenes: List[ScriptScene] = field(default_factory=list)
    total_duration: float = 0.0
    total_words: int = 0
    hook_text: str = ""                  # First 5 seconds narration
    hook_question: str = ""              # Short bold question/statement for visual overlay (≤40 chars)
    cta_text: str = ""                   # Call to action
    description_seo: str = ""            # For YouTube description
    tags: List[str] = field(default_factory=list)
    thumbnail_concept: str = ""          # Concept for thumbnail generation

    def to_assembler_format(self) -> List[Dict]:
        """Convert to the format expected by VideoAssembler."""
        return [
            {
                "scene_id": s.scene_id,
                "text": s.narration,
                "visual_description": s.visual_cue,
                "on_screen_text": s.on_screen_text,
                "duration_estimate": s.duration_estimate,
            }
            for s in self.scenes
        ]


# ═══════════════════════════════════════════════════════════════
#  PART 2: TOPIC RESEARCHER
# ═══════════════════════════════════════════════════════════════

# Niche definitions: seed keywords + subreddits + YouTube category
NICHE_CONFIG = {
    "ai_tools": {
        "seeds": ["Claude AI Developer", "AI Driven Machine", "Claude", "AI automation",
                   "AI Automation", "AI news", "Tools with AI", "AI productivity"],
        "subreddits": [ "Claude", "Claude Code", "USA Government Claude",
                        "Govenrments with Artificial Intellegence", "Claude", "AI Impacting Jobs"],
        "youtube_category": "28",
        "news_keywords": "AI Impacting Jobs OR Claude Code OR ChatGPT OR Govenrments with Artificial Intellegence",
    },
    "finance": {
        "seeds": ["investing", "stock market", "personal finance", "cryptocurrency",
                   "passive income", "financial freedom", "money management", "budgeting"],
        "subreddits": ["personalfinance", "investing", "stocks", "CryptoCurrency",
                        "financialindependence", "wallstreetbets"],
        "youtube_category": "22",
        "news_keywords": "stock market OR investing OR cryptocurrency OR personal finance",
    },
    "self_improvement": {
        "seeds": ["productivity", "stoicism", "self improvement", "habits",
                   "mindset", "motivation", "discipline", "mental health"],
        "subreddits": ["selfimprovement", "productivity", "getdisciplined",
                        "Stoicism", "DecidingToBeBetter", "mindfulness"],
        "youtube_category": "22",
        "news_keywords": "productivity OR self improvement OR mindset",
    },
    "tech": {
        "seeds": ["technology", "gadgets", "software", "cybersecurity",
                   "smartphone", "programming", "cloud computing", "tech news"],
        "subreddits": ["technology", "gadgets", "programming", "netsec",
                        "Android", "apple", "hardware"],
        "youtube_category": "28",
        "news_keywords": "technology OR cybersecurity OR software OR gadgets",
    },
    "health": {
        "seeds": ["health science", "body science", "sleep science", "nutrition",
                   "exercise", "brain health", "psychology facts", "wellness"],
        "subreddits": ["science", "Health", "nutrition", "Fitness",
                        "Nootropics", "sleep", "bodyweightfitness"],
        "youtube_category": "26",
        "news_keywords": "health science OR nutrition OR sleep OR brain",
    },
    "history": {
        "seeds": ["history", "ancient civilizations", "historical mysteries",
                   "world war", "historical facts", "lost civilizations", "archaeology"],
        "subreddits": ["history", "AskHistorians", "HistoryPorn",
                        "todayilearned", "Archaeology", "AncientCivilizations"],
        "youtube_category": "27",
        "news_keywords": "historical discovery OR archaeology OR ancient history",
    },
    "tech_gadgets": {
        "seeds": [
            # Mobile & wearables
            "latest smartphone 2026", "best smartphone camera 2026", "new iPhone 2026",
            "smartwatch 2026 review", "smart glasses AR 2026", "AI earbuds 2026",
            "smart contact lenses 2026", "foldable phone 2026", "rollable display phone 2026",
            # Cameras & drones
            "best mirrorless camera 2026", "drone review 2026", "FPV drone 2026",
            "action camera 2026", "AI camera 2026",
            # Gaming
            "PlayStation 6 2026", "Xbox 2026", "gaming handheld 2026",
            "VR headset 2026", "next gen gaming 2026",
            # AI-driven gadgets
            "AI gadgets 2026", "AI home robot 2026", "Meta Ray-Ban smart glasses 2026",
            "smart home devices 2026", "AI wearable 2026", "AI smart ring 2026",
            # Sports & health tech
            "sports gadgets 2026", "fitness tracker 2026", "smart ring 2026",
            "GPS sports watch 2026", "AI health wearable 2026",
            # Emerging tech
            "spatial computing 2026", "holographic display 2026",
            "noise cancelling earbuds 2026", "brain computer interface 2026",
            "exoskeleton suit 2026", "smart bike 2026",
        ],
        "subreddits": [
            "gadgets", "smartphones", "Android", "apple",
            "drones", "Cameras", "gaming", "hardware", "tech",
            "virtualreality", "PS5", "wearables", "smartwatch",
        ],
        "youtube_category": "28",   # Science & Technology
        "news_keywords": (
            "smartphone 2026 OR smartwatch 2026 OR drone 2026 OR gadget 2026 OR "
            "wearable 2026 OR camera 2026 OR PlayStation 6 OR VR headset 2026 OR "
            "smart glasses 2026 OR earbuds 2026 OR foldable phone 2026 OR "
            "AI device 2026 OR smart ring 2026 OR holographic 2026"
        ),
    },
    "heritage": {
        "seeds": [
            "ancient India history", "lost Hindu temples", "Buddhist heritage India",
            "Angkor Wat history", "ancient civilizations Asia", "Hindu Buddhist kingdoms",
            "Takshashila university ancient", "lost history India", "ancient Indian architecture",
            "Hindu temples Afghanistan", "Bali Hindu culture", "Zoroastrian history Persia",
            "archaeology India secrets", "untold Indian history", "ancient trade routes India",
            "Nalanda university history", "Vijayanagara empire", "Chola dynasty temples",
            "ancient India Southeast Asia", "Buddhist monuments India",
        ],
        "subreddits": [
            "IndiaSpeaks", "IndianHistory", "hinduism", "Buddhism",
            "AncientCivilizations", "Archaeology", "AskHistorians", "HistoryPorn",
        ],
        "youtube_category": "27",   # Education
        "news_keywords": (
            "ancient India OR Hindu temple OR Buddhist heritage OR archaeology India OR "
            "lost civilization OR ancient history OR Hindu kingdom OR Buddhist monastery"
        ),
    },
    "world_crisis": {
        "seeds": [
            "world war 3", "nuclear threat", "global conflict", "geopolitics",
            "war news today", "world crisis", "NATO", "Russia Ukraine war",
            "Middle East conflict", "global threat", "military escalation",
            "world panic", "superpower conflict", "nuclear war risk",
            "global emergency", "world news breaking",
        ],
        "subreddits": [
            "worldnews", "geopolitics", "CredibleDefense",
            "europe", "UkraineWarVideoReport", "GlobalTalk", "news",
        ],
        "youtube_category": "25",   # News & Politics
        "news_keywords": (
            "world war OR nuclear threat OR military conflict OR NATO OR "
            "geopolitics OR global crisis OR war news OR invasion OR escalation"
        ),
    },
}


class TopicResearcher:
    """
    Multi-source trending topic discovery engine.

    Aggregates topics from Google Trends, YouTube, Reddit, and news APIs,
    scores them by virality and niche relevance, deduplicates, and returns
    ranked topic candidates for video creation.

    Usage:
        researcher = TopicResearcher(niche="ai_tools")
        topics = researcher.discover(count=5)
        for t in topics:
            print(f"[{t.score:.0f}] {t.title} ({t.source})")
    """

    def __init__(
        self,
        niche: str = "ai_tools",
        db_path: str = "data/topics.db",
        custom_seeds: Optional[List[str]] = None,
    ):
        self.niche = niche
        self.niche_cfg = NICHE_CONFIG.get(niche, NICHE_CONFIG["ai_tools"])
        if custom_seeds:
            self.niche_cfg["seeds"] = custom_seeds
        self.db_path = db_path
        self.logger = logging.getLogger("TopicResearcher")
        self._init_db()

    # ─── PUBLIC API ───────────────────────────────────────────────────

    def discover(
        self,
        count: int = 5,
        sources: Optional[List[str]] = None,
        exclude_used: bool = True,
    ) -> List[Topic]:
        """
        Discover and rank trending topics.

        Args:
            count: Number of top topics to return
            sources: Which sources to query (default: all available)
                     Options: "google_trends", "youtube", "reddit", "news"
            exclude_used: Skip topics already used in previous videos

        Returns:
            Sorted list of Topic objects, highest score first
        """
        if sources is None:
            sources = ["google_trends", "youtube", "reddit", "news"]

        all_topics = []

        # Gather from each source
        for source in sources:
            try:
                fetcher = self._get_fetcher(source)
                topics = fetcher()
                all_topics.extend(topics)
                self.logger.info(f"  {source}: found {len(topics)} topics")
            except Exception as e:
                self.logger.warning(f"  {source} failed: {e}")

        if not all_topics:
            self.logger.warning("No topics found from any source. Using seed topics.")
            all_topics = self._fallback_seed_topics()

        # Deduplicate
        all_topics = self._deduplicate(all_topics)

        # Exclude previously used
        if exclude_used:
            used_fps = self._get_used_fingerprints()
            all_topics = [t for t in all_topics if t.fingerprint not in used_fps]

        # Score and rank
        all_topics = self._score_topics(all_topics)
        all_topics.sort(key=lambda t: t.score, reverse=True)

        result = all_topics[:count]
        self.logger.info(f"Top {len(result)} topics selected:")
        for t in result:
            self.logger.info(f"  [{t.score:.0f}] {t.title} ({t.source})")

        return result

    def mark_used(self, topic: Topic):
        """Mark a topic as used (so it won't be suggested again)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO used_topics (fingerprint, title, used_at) VALUES (?, ?, ?)",
            (topic.fingerprint, topic.title, datetime.datetime.utcnow().isoformat()),
        )
        conn.commit()
        conn.close()

    # ─── SOURCE FETCHERS ──────────────────────────────────────────────

    def _get_fetcher(self, source: str):
        fetchers = {
            "google_trends": self._fetch_google_trends,
            "youtube": self._fetch_youtube_trending,
            "reddit": self._fetch_reddit,
            "news": self._fetch_news,
        }
        return fetchers.get(source, lambda: [])

    def _fetch_google_trends(self) -> List[Topic]:
        """
        Fetch rising/top related queries from Google Trends via pytrends.

        Note: trending_searches() and realtime_trending_searches() were
        deprecated by Google (404 as of 2026). We use related_queries()
        and interest_over_time() which remain functional.
        """
        from pytrends.request import TrendReq

        # Do NOT pass retries/backoff_factor — breaks with urllib3 2.x
        pytrends = TrendReq(hl="en-US", tz=330)
        topics   = []
        seeds    = self.niche_cfg["seeds"][:3]   # max 3 seeds to stay under rate limit

        rate_limited = False   # circuit breaker — trips on first 429

        for seed in seeds:
            if rate_limited:
                break

            try:
                pytrends.build_payload([seed], timeframe="now 7-d")
                related = pytrends.related_queries()

                for kind, trend_label in (("rising", "rising"), ("top", "stable")):
                    df = (related.get(seed) or {}).get(kind)
                    if df is None or df.empty:
                        continue
                    for _, row in df.head(5).iterrows():
                        query = row.get("query", "").strip()
                        if query:
                            topics.append(Topic(
                                title=query,
                                source="google_trends",
                                keyword=query,
                                search_volume_trend=trend_label,
                                related_keywords=[seed],
                                score=float(row.get("value", 50)),
                            ))

                time.sleep(3)   # polite pause between seeds

            except Exception as e:
                if "429" in str(e):
                    self.logger.warning(
                        "google_trends: rate-limited — skipping remaining seeds "
                        "and falling back to Reddit/News sources."
                    )
                    rate_limited = True   # trip circuit breaker, exit loop
                else:
                    self.logger.warning(f"google_trends: '{seed}' failed: {e}")

        self.logger.debug(f"google_trends: {len(topics)} rising/top queries found")
        return topics

    def _fetch_youtube_trending(self) -> List[Topic]:
        """Fetch trending/popular videos in the niche from YouTube Data API."""
        api_key = os.environ.get("YOUTUBE_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            self.logger.debug("No YOUTUBE_API_KEY set, skipping YouTube source")
            return []

        from googleapiclient.discovery import build
        youtube = build("youtube", "v3", developerKey=api_key)

        topics = []

        # Search for recent popular videos in niche
        for seed in self.niche_cfg["seeds"][:3]:
            try:
                response = youtube.search().list(
                    part="snippet",
                    q=seed,
                    type="video",
                    order="viewCount",
                    publishedAfter=(
                        datetime.datetime.utcnow() - datetime.timedelta(days=7)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    maxResults=5,
                    relevanceLanguage="en",
                ).execute()

                for item in response.get("items", []):
                    snippet = item["snippet"]
                    topics.append(Topic(
                        title=snippet["title"],
                        source="youtube",
                        keyword=seed,
                        description=snippet.get("description", "")[:200],
                        source_url=f"https://youtube.com/watch?v={item['id']['videoId']}",
                    ))
            except Exception as e:
                self.logger.debug(f"YouTube search for '{seed}' failed: {e}")

        return topics

    def _fetch_reddit(self) -> List[Topic]:
        """Fetch hot/rising posts from niche subreddits."""
        client_id = os.environ.get("REDDIT_CLIENT_ID")
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET")

        if not client_id or not client_secret:
            # Fallback: use Reddit JSON API (no auth needed, rate limited)
            return self._fetch_reddit_noauth()

        import praw
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent="FacelessYT/1.0 (topic research)",
        )

        topics = []
        for sub_name in self.niche_cfg["subreddits"][:4]:
            try:
                subreddit = reddit.subreddit(sub_name)
                for post in subreddit.hot(limit=10):
                    if post.score < 50:
                        continue
                    topics.append(Topic(
                        title=post.title,
                        source="reddit",
                        keyword=sub_name,
                        description=post.selftext[:200] if post.selftext else "",
                        source_url=f"https://reddit.com{post.permalink}",
                        score=min(post.score / 100, 50),  # raw score capped
                    ))
            except Exception as e:
                self.logger.debug(f"Reddit r/{sub_name} failed: {e}")

        return topics

    def _fetch_reddit_noauth(self) -> List[Topic]:
        """Fetch from Reddit without authentication (public JSON endpoints)."""
        import requests

        topics = []
        headers = {"User-Agent": "FacelessYT/1.0"}

        for sub_name in self.niche_cfg["subreddits"][:3]:
            try:
                resp = requests.get(
                    f"https://www.reddit.com/r/{sub_name}/hot.json?limit=10",
                    headers=headers, timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for child in data.get("data", {}).get("children", []):
                        post = child.get("data", {})
                        if post.get("score", 0) < 50:
                            continue
                        topics.append(Topic(
                            title=post.get("title", ""),
                            source="reddit",
                            keyword=sub_name,
                            source_url=f"https://reddit.com{post.get('permalink', '')}",
                        ))
                time.sleep(2)  # respect rate limits
            except Exception as e:
                self.logger.debug(f"Reddit no-auth r/{sub_name} failed: {e}")

        return topics

    def _fetch_news(self) -> List[Topic]:
        """Fetch recent news headlines from NewsAPI or GNews."""
        api_key = os.environ.get("NEWSAPI_KEY")

        if api_key:
            return self._fetch_newsapi(api_key)

        # Fallback: use RSS feeds (free, no API key needed)
        return self._fetch_rss_news()

    def _fetch_newsapi(self, api_key: str) -> List[Topic]:
        """Fetch from NewsAPI.org."""
        import requests

        topics = []
        keywords = self.niche_cfg["news_keywords"]

        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": keywords,
                    "sortBy": "popularity",
                    "language": "en",
                    "pageSize": 15,
                    "apiKey": api_key,
                },
                timeout=10,
            )
            if resp.status_code == 200:
                for article in resp.json().get("articles", []):
                    topics.append(Topic(
                        title=article.get("title", ""),
                        source="news",
                        description=article.get("description", "")[:200],
                        source_url=article.get("url", ""),
                    ))
        except Exception as e:
            self.logger.debug(f"NewsAPI failed: {e}")

        return topics

    def _fetch_rss_news(self) -> List[Topic]:
        """Fallback: fetch from public RSS feeds (no API key needed)."""
        import feedparser

        topics = []
        # Tech/AI RSS feeds
        feeds = {
            "ai_tools": [
                "https://techcrunch.com/category/artificial-intelligence/feed/",
                "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
            ],
            "finance": [
                "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
            ],
            "tech": [
                "https://techcrunch.com/feed/",
                "https://www.theverge.com/rss/index.xml",
            ],
            "health": [
                "https://www.sciencedaily.com/rss/health_medicine.xml",
            ],
        }

        niche_feeds = feeds.get(self.niche, feeds.get("tech", []))

        for feed_url in niche_feeds[:2]:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:10]:
                    topics.append(Topic(
                        title=entry.get("title", ""),
                        source="news",
                        description=entry.get("summary", "")[:200],
                        source_url=entry.get("link", ""),
                    ))
            except Exception as e:
                self.logger.debug(f"RSS feed failed: {e}")

        return topics

    def _fallback_seed_topics(self) -> List[Topic]:
        """Generate topics from seed keywords when all sources fail."""
        return [
            Topic(
                title=f"{seed} — What You Need to Know in {datetime.date.today().year}",
                source="seed",
                keyword=seed,
                search_volume_trend="evergreen",
            )
            for seed in self.niche_cfg["seeds"][:5]
        ]

    # ─── SCORING & RANKING ────────────────────────────────────────────

    def _score_topics(self, topics: List[Topic]) -> List[Topic]:
        """
        Score each topic on a 0–100 scale based on multiple signals.

        Scoring weights:
            - Source reliability (YouTube trending > Google Trends > Reddit > News)
            - Trend momentum ("breakout" > "rising" > "trending" > "stable")
            - Niche relevance (keyword overlap with seeds)
            - Novelty (not covered by competitor channels recently)
            - Title quality (length, specificity)
        """
        for topic in topics:
            score = 0.0

            # Source weight (0–25)
            source_weights = {
                "google_trends": 22, "youtube": 25, "reddit": 18,
                "news": 20, "seed": 10,
            }
            score += source_weights.get(topic.source, 10)

            # Trend momentum (0–25)
            trend_weights = {
                "breakout": 25, "rising": 20, "trending": 15,
                "stable": 8, "evergreen": 12, "": 10,
            }
            score += trend_weights.get(topic.search_volume_trend, 10)

            # Niche relevance (0–30)
            seeds_lower = [s.lower() for s in self.niche_cfg["seeds"]]
            title_lower = topic.title.lower()
            keyword_lower = topic.keyword.lower()

            matches = sum(1 for s in seeds_lower if s in title_lower or s in keyword_lower)
            partial = sum(1 for s in seeds_lower
                         for word in s.split() if word in title_lower)
            relevance = min((matches * 10 + partial * 3), 30)
            score += relevance
            topic.niche_relevance = relevance / 30

            # Title quality (0–20)
            title_len = len(topic.title)
            if 30 <= title_len <= 80:
                score += 15
            elif 20 <= title_len <= 120:
                score += 10
            else:
                score += 5

            # Bonus for specificity (numbers, years, "how to", "why")
            specificity_triggers = ["2026", "2025", "how", "why", "best", "top",
                                     "new", "secret", "truth", "mistake", "never"]
            if any(trigger in title_lower for trigger in specificity_triggers):
                score += 5

            topic.score = min(score, 100)

        return topics

    # ─── DEDUPLICATION ────────────────────────────────────────────────

    def _deduplicate(self, topics: List[Topic]) -> List[Topic]:
        """Remove near-duplicate topics based on fingerprint and fuzzy title matching."""
        seen = {}
        unique = []

        for topic in topics:
            if not topic.title.strip():
                continue

            # Exact fingerprint match
            if topic.fingerprint in seen:
                # Keep the one with higher score
                if topic.score > seen[topic.fingerprint].score:
                    unique = [t for t in unique if t.fingerprint != topic.fingerprint]
                    unique.append(topic)
                    seen[topic.fingerprint] = topic
                continue

            # Simple fuzzy: normalise and check overlap
            normalised = re.sub(r'[^a-z0-9\s]', '', topic.title.lower()).strip()
            is_dupe = False
            for existing_fp, existing in seen.items():
                existing_norm = re.sub(r'[^a-z0-9\s]', '', existing.title.lower()).strip()
                # Check if >70% of words overlap
                words_a = set(normalised.split())
                words_b = set(existing_norm.split())
                if words_a and words_b:
                    overlap = len(words_a & words_b) / max(len(words_a), len(words_b))
                    if overlap > 0.7:
                        is_dupe = True
                        break

            if not is_dupe:
                unique.append(topic)
                seen[topic.fingerprint] = topic

        return unique

    # ─── DATABASE ─────────────────────────────────────────────────────

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path) if os.path.dirname(self.db_path) else ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS used_topics (
                fingerprint TEXT PRIMARY KEY,
                title TEXT,
                used_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _get_used_fingerprints(self) -> set:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT fingerprint FROM used_topics").fetchall()
        conn.close()
        return {r[0] for r in rows}


# ═══════════════════════════════════════════════════════════════
#  PART 3: SCRIPT WRITER
# ═══════════════════════════════════════════════════════════════

class ScriptWriter:
    """
    AI-powered YouTube script generator with retention optimisation.

    Generates structured, scene-by-scene scripts with:
        - Hook in first 5 seconds (curiosity gap)
        - Pattern interrupts every 60-90 seconds
        - Visual cues per scene (for image/video generation)
        - On-screen text callouts
        - CTA placement
        - Thumbnail concept

    Supports Claude (Anthropic) and GPT (OpenAI) as backends.

    Usage:
        writer = ScriptWriter(provider="anthropic")
        script = writer.generate(
            topic="How AI is Replacing Entire Marketing Teams",
            video_length=10,
            tone="engaging_serious",
        )

        for scene in script.scenes:
            print(f"[{scene.scene_type}] {scene.narration[:80]}...")
    """

    # Tone presets
    TONE_PROMPTS = {
        "engaging_serious": "Professional but engaging. Use clear, confident language. Explain complex topics simply. No fluff.",
        "casual_fun": "Casual and entertaining. Use conversational language, light humor, and relatable examples.",
        "dramatic_storytelling": "Dramatic narrative style. Build tension, use cliffhangers between scenes, vivid descriptions.",
        "educational": "Clear educational tone. Step-by-step explanations. Use analogies to simplify concepts.",
        "motivational": "Inspiring and energetic. Use powerful language, rhetorical questions, and call-to-action moments.",
    }

    def __init__(
        self,
        provider: str = "anthropic",
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.provider = provider
        self.api_key = api_key or os.environ.get(
            "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
        )
        self.model = model or (
            "claude-sonnet-4-20250514" if provider == "anthropic" else "gpt-4o-mini"
        )
        self.logger = logging.getLogger("ScriptWriter")

    # ─── PUBLIC API ───────────────────────────────────────────────────

    def generate(
        self,
        topic: str | Topic,
        video_length: int = 10,
        tone: str = "engaging_serious",
        target_audience: str = "",
        niche: str = "tech",
        include_shorts: bool = False,
        custom_instructions: str = "",
    ) -> Script:
        """
        Generate a full video script.

        Args:
            topic: Topic string or Topic object from researcher
            video_length: Target video length in minutes (5–15 recommended)
            tone: One of TONE_PROMPTS keys or custom tone description
            target_audience: Description of target viewer
            niche: Content niche for context
            include_shorts: Also generate a 60-second Shorts script variant
            custom_instructions: Any additional instructions for the LLM

        Returns:
            Script object with scenes ready for video assembly
        """
        topic_str = topic.title if isinstance(topic, Topic) else topic
        topic_context = ""
        if isinstance(topic, Topic):
            topic_context = (
                f"Source: {topic.source}\n"
                f"Trend: {topic.search_volume_trend}\n"
                f"Related keywords: {', '.join(topic.related_keywords[:5])}\n"
                f"Context: {topic.description[:200]}"
            )

        # Calculate target words (avg speaking rate: 150 words/minute for natural TTS)
        target_words = video_length * 150
        num_scenes = max(4, video_length)  # roughly 1 scene per minute

        is_reel = video_length <= 1
        if is_reel:
            prompt = self._build_reel_prompt(
                topic=topic_str, topic_context=topic_context,
                tone=tone, niche=niche,
                custom_instructions=custom_instructions,
            )
        else:
            prompt = self._build_prompt(
                topic=topic_str,
                topic_context=topic_context,
                target_words=target_words,
                num_scenes=num_scenes,
                tone=tone,
                target_audience=target_audience,
                niche=niche,
                custom_instructions=custom_instructions,
            )

        self.logger.info(f"Generating script: '{topic_str}' ({video_length} min, {num_scenes} scenes)")

        # Call LLM
        raw_response = self._call_llm(prompt)

        # Parse response
        script = self._parse_response(raw_response, topic_str)

        # Calculate durations
        for scene in script.scenes:
            words = len(scene.narration.split())
            scene.duration_estimate = words / 2.5  # ~2.5 words/second for natural TTS

        script.total_words = sum(len(s.narration.split()) for s in script.scenes)
        script.total_duration = sum(s.duration_estimate for s in script.scenes)

        self.logger.info(
            f"Script generated: {len(script.scenes)} scenes, "
            f"{script.total_words} words, ~{script.total_duration/60:.1f} min"
        )

        # Generate Shorts variant if requested
        if include_shorts:
            shorts_script = self._generate_shorts_variant(topic_str, script)
            # Store as attribute for the caller to access
            script._shorts_variant = shorts_script

        return script

    def refine(self, script: Script, feedback: str) -> Script:
        """
        Refine a generated script based on feedback.

        Useful for iterative improvement after reviewing analytics
        or after initial human review.
        """
        scenes_text = "\n\n".join(
            f"Scene {s.scene_id} [{s.scene_type}]:\n{s.narration}"
            for s in script.scenes
        )

        prompt = f"""You are a YouTube script editor. Refine this script based on the feedback.

CURRENT SCRIPT:
Topic: {script.topic}

{scenes_text}

FEEDBACK:
{feedback}

Return the improved script in the same JSON format as before.
Include ALL scenes (modified and unmodified).
Output ONLY JSON, no markdown or explanation."""

        raw = self._call_llm(prompt)
        return self._parse_response(raw, script.topic)

    # ─── PROMPT ENGINEERING ───────────────────────────────────────────

    def _build_reel_prompt(self, topic, topic_context, tone, niche,
                            custom_instructions="") -> str:
        """
        Reel-specific prompt (≤60s).  Laser-focused on stopping the scroll
        within the first 2 seconds using proven hook formulas.
        """
        tone_desc = self.TONE_PROMPTS.get(tone, tone)
        return f"""You are a viral Hinglish short-form video scriptwriter for Indian audiences. You MUST write every narration line in HINGLISH — a natural mix of Hindi and English written in Roman script. Pure English scripts will be rejected.

TOPIC: {topic}
{f"CONTEXT: {topic_context}" if topic_context else ""}
NICHE: {niche}
TONE: {tone_desc}
{f"ADDITIONAL: {custom_instructions}" if custom_instructions else ""}

⚠️ MANDATORY LANGUAGE RULE — HINGLISH ONLY (Roman script, NO Devanagari):
  - Every sentence MUST contain Hindi words mixed with English
  - Use Hindi for: emotions, emphasis, connectors, reactions, conversational filler
  - Keep in English: technical terms, brand names, numbers, proper nouns
  - Target ratio: ~55% English words, ~45% Hindi words
  - CORRECT: "Yaar, Claude ka source code leak ho gaya — aur jo secrets saamne aaye, wo mind-blowing hain!"
  - CORRECT: "Sach mein, ye AI model internally sochti hai pehle — literally ek internal monologue hai!"
  - WRONG: "Claude's source code leaked and the secrets were mind-blowing." ← Pure English, NOT allowed
  - Never use Devanagari script (ElevenLabs reads Roman only)

TARGET: A 45-60 second reel script (~120 words of narration). Every word earns its place.

HOOK FORMULA — Pick the strongest one for this topic (write it in Hinglish):
  A) SHOCKING STAT:   "X% of people have no idea that..."
  B) BOLD CLAIM:      "This single [thing] is destroying your [outcome]..."
  C) OPEN LOOP:       "What happened next shocked everyone, including the experts..."
  D) DIRECT QUESTION: "Are you making this [topic] mistake right now?"
  E) CONTROVERSY:     "Everyone says [X] — but they are completely wrong."

REEL STRUCTURE (strict):
  Scene 0 [hook]    — 1 sentence. The scroll-stopper. Uses one of the formulas above. NO filler words.
  Scene 1 [content] — Core insight / story beat 1. Build tension or curiosity.
  Scene 2 [content] — Core insight / story beat 2. Deliver value or twist.
  Scene 3 [cta]     — End with an incomplete thought or direct question to force a comment ("Comment below if...").

RULES:
- First word must NOT be "I", "Hey", "So", "Today", "Welcome", or "In this video"
- hook_question must be ≤ 40 characters — it will be shown as a bold text overlay on screen
- No scene narration longer than 40 words
- hook_question must be a SHORT punchy question or statement that appears on screen (≤40 chars)

OUTPUT — Return ONLY valid JSON:
{{
  "title": "Reel title (40-60 chars, high curiosity)",
  "hook_text": "The exact first spoken sentence",
  "hook_question": "Short bold question for screen overlay ≤40 chars",
  "scenes": [
    {{"scene_id": 0, "scene_type": "hook", "narration": "...", "visual_cue": "...", "on_screen_text": "...", "transition": "cut"}},
    {{"scene_id": 1, "scene_type": "content", "narration": "...", "visual_cue": "...", "on_screen_text": "...", "transition": "cut"}},
    {{"scene_id": 2, "scene_type": "content", "narration": "...", "visual_cue": "...", "on_screen_text": "...", "transition": "cut"}},
    {{"scene_id": 3, "scene_type": "cta", "narration": "...", "visual_cue": "...", "on_screen_text": "...", "transition": "fade"}}
  ],
  "cta_text": "Comment call-to-action",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "thumbnail_concept": "Eye-catching thumbnail idea",
  "description_seo": "2-sentence reel description with keywords"
}}"""

    def _build_prompt(
        self, topic, topic_context, target_words, num_scenes,
        tone, target_audience, niche, custom_instructions,
    ) -> str:
        tone_desc = self.TONE_PROMPTS.get(tone, tone)

        return f"""You are an elite Hinglish YouTube scriptwriter for Indian audiences. You write in HINGLISH — natural Hindi+English mix in Roman script. Pure English narration is NOT acceptable. Every sentence must blend both languages.

TASK: Write a complete {num_scenes}-scene Hinglish YouTube script about the following topic.

TOPIC: {topic}
{f"CONTEXT: {topic_context}" if topic_context else ""}
TARGET LENGTH: ~{target_words} words ({target_words // 150} minutes at natural speaking pace)
TONE: {tone_desc}
NICHE: {niche}
TARGET AUDIENCE: {target_audience or "General YouTube audience interested in " + niche}
{f"ADDITIONAL INSTRUCTIONS: {custom_instructions}" if custom_instructions else ""}

⚠️ MANDATORY LANGUAGE RULE — HINGLISH ONLY (Roman script, NO Devanagari):
  - Every sentence MUST contain Hindi words mixed with English
  - Use Hindi for: emotions, emphasis, connectors, reactions, conversational filler
  - Keep in English: technical terms, brand names, numbers, proper nouns
  - Target ratio: ~55% English words, ~45% Hindi words
  - CORRECT: "Yaar, ye technology itni powerful hai ki duniya badal gayi — literally har cheez."
  - CORRECT: "Lekin baat yahan khatam nahi hoti — asli twist toh aage hai."
  - WRONG: "This technology is so powerful it changed the world." ← Pure English, NOT allowed
  - Never use Devanagari script

RETENTION RULES (follow these strictly):
1. HOOK (first 10 seconds): Start with a bold claim, surprising fact, or provocative question. 
   Never start with "Hey guys" or "Welcome to my channel". Jump straight into value.
2. OPEN LOOP: Within the first 30 seconds, tease something that will be revealed later 
   ("But here is what most people get wrong..." or "By the end, you will understand why...").
3. PATTERN INTERRUPTS: Every 60-90 seconds, change the energy — pose a question, 
   share a counterintuitive fact, use a micro-story, or change the visual pace.
4. CURIOSITY GAPS: Between major points, hint at what is coming next to prevent clicks away.
5. CTA: Include ONE subscribe CTA naturally at the ~70% mark (not the beginning).
6. OUTRO: End with a thought-provoking final statement, not a generic "thanks for watching".

OUTPUT FORMAT — Return ONLY a valid JSON object (no markdown, no explanation):
{{
  "title": "YouTube-optimised title (50-70 chars, primary keyword near start)",
  "hook_text": "The exact first sentence (for first 5 seconds)",
  "scenes": [
    {{
      "scene_id": 0,
      "scene_type": "hook",
      "narration": "Full narration text for this scene...",
      "visual_cue": "Detailed description for AI image/video generation",
      "on_screen_text": "Key phrase to overlay on screen (short, impactful)",
      "transition": "cut"
    }},
    {{
      "scene_id": 1,
      "scene_type": "content",
      "narration": "...",
      "visual_cue": "...",
      "on_screen_text": "...",
      "transition": "fade"
    }}
  ],
  "cta_text": "Subscribe call-to-action text",
  "tags": ["tag1", "tag2", "tag3", ...],
  "thumbnail_concept": "Description of an eye-catching thumbnail idea",
  "description_seo": "SEO-optimised YouTube description (2-3 sentences)"
}}

SCENE TYPES to use:
- "hook": Opening hook (scene 0, first 10-15 seconds)
- "content": Main content scenes
- "pattern_interrupt": Energy shift / surprising tangent / question
- "cta": Subscribe/engagement call to action
- "outro": Closing statement

VISUAL CUE GUIDELINES:
- Be specific: "Close-up of a glowing neural network with blue nodes" NOT "AI image"
- Include composition: "Wide shot", "Close-up", "Split screen", "Text on dark background"
- Include mood: "Dramatic lighting", "Clean minimal", "High contrast", "Futuristic"
- Each scene should have a visually DISTINCT cue (no repetition)

Write {num_scenes} scenes totalling approximately {target_words} words of narration."""

    # ─── LLM CALLS ────────────────────────────────────────────────────

    def _call_llm(self, prompt: str) -> str:
        """Route to the configured LLM provider."""
        if self.provider == "anthropic":
            return self._call_anthropic(prompt)
        elif self.provider == "openai":
            return self._call_openai(prompt)
        elif self.provider == "gemini":
            return self._call_gemini(prompt)
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    def _call_anthropic(self, prompt: str) -> str:
        import anthropic
        client = anthropic.Anthropic(
            api_key=self.api_key,
            timeout=60.0,   # 60s hard timeout — prevents infinite hangs
        )
        response = client.messages.create(
            model=self.model,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def _call_openai(self, prompt: str) -> str:
        import openai
        client = openai.OpenAI(api_key=self.api_key)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "You are a YouTube scriptwriter. Output only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=8000,
            temperature=0.8,
        )
        return response.choices[0].message.content

    def _call_gemini(self, prompt: str) -> str:
        import requests
        api_key = self.api_key or os.environ.get("GOOGLE_API_KEY")
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

    # ─── RESPONSE PARSING ─────────────────────────────────────────────

    def _parse_response(self, raw: str, topic: str) -> Script:
        """Parse LLM JSON response into a Script object."""
        # Clean JSON from potential markdown wrapping
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            # Remove ```json ... ``` wrapper
            lines = cleaned.split("\n")
            start = next((i for i, l in enumerate(lines) if l.strip().startswith("{")), 1)
            end = next((i for i in range(len(lines)-1, -1, -1) if lines[i].strip().startswith("}")), len(lines)-1)
            cleaned = "\n".join(lines[start:end+1])

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON parse failed: {e}")
            self.logger.debug(f"Raw response: {raw[:500]}...")
            # Emergency fallback: create basic script
            return self._emergency_fallback_script(topic)

        scenes = []
        for s in data.get("scenes", []):
            scenes.append(ScriptScene(
                scene_id=s.get("scene_id", len(scenes)),
                narration=s.get("narration", ""),
                visual_cue=s.get("visual_cue", ""),
                scene_type=s.get("scene_type", "content"),
                on_screen_text=s.get("on_screen_text", ""),
                transition=s.get("transition", "cut"),
            ))

        return Script(
            topic=topic,
            title=data.get("title", topic),
            scenes=scenes,
            hook_text=data.get("hook_text", ""),
            hook_question=data.get("hook_question", ""),
            cta_text=data.get("cta_text", ""),
            tags=data.get("tags", []),
            thumbnail_concept=data.get("thumbnail_concept", ""),
            description_seo=data.get("description_seo", ""),
        )

    def _emergency_fallback_script(self, topic: str) -> Script:
        """Create a minimal script when LLM parsing fails entirely."""
        self.logger.warning("Using emergency fallback script")
        return Script(
            topic=topic,
            title=topic,
            scenes=[
                ScriptScene(0, f"What if everything you thought about {topic} was wrong?",
                           "Dramatic text on dark background", scene_type="hook"),
                ScriptScene(1, f"Today we are going to explore {topic} in depth.",
                           "Wide establishing shot related to topic", scene_type="content"),
                ScriptScene(2, f"That is the key takeaway about {topic}. If you found this valuable, subscribe for more.",
                           "Animated subscribe button overlay", scene_type="cta"),
            ],
        )

    # ─── SHORTS VARIANT ───────────────────────────────────────────────

    def _generate_shorts_variant(self, topic: str, full_script: Script) -> Script:
        """Generate a 60-second Shorts version from the full script."""
        prompt = f"""Condense this YouTube script into a 60-second YouTube Shorts version.

ORIGINAL TOPIC: {topic}
ORIGINAL HOOK: {full_script.hook_text}
KEY POINTS: {' | '.join(s.narration[:100] for s in full_script.scenes[:4])}

Rules for Shorts:
- Maximum 150 words (60 seconds at fast pace)
- Hook in first 2 seconds (one punchy sentence)
- 3-4 rapid-fire points
- End with a curiosity hook ("Full video on our channel")
- Visual cues for VERTICAL (9:16) format

Return ONLY JSON with same format as the original script but 3-4 scenes max."""

        raw = self._call_llm(prompt)
        return self._parse_response(raw, f"{topic} (Shorts)")


# ═══════════════════════════════════════════════════════════════
#  PART 4: PIPELINE INTEGRATION
# ═══════════════════════════════════════════════════════════════

def discover_and_write(
    niche: str = "ai_tools",
    provider: str = "anthropic",
    video_length: int = 10,
    tone: str = "engaging_serious",
    count: int = 1,
) -> List[Tuple[Topic, Script]]:
    """
    End-to-end: discover trending topics → generate scripts.

    Returns list of (Topic, Script) tuples ready for video assembly.

    Usage:
        results = discover_and_write(niche="ai_tools", count=3)
        for topic, script in results:
            print(f"Topic: {topic.title}")
            print(f"Script: {script.title} ({len(script.scenes)} scenes)")
    """
    logger = logging.getLogger("Pipeline")

    # Step 1: Discover topics
    logger.info(f"Discovering {count} topics in '{niche}' niche...")
    researcher = TopicResearcher(niche=niche)
    topics = researcher.discover(count=count)

    if not topics:
        logger.error("No topics found!")
        return []

    # Step 2: Generate scripts
    writer = ScriptWriter(provider=provider)
    results = []

    for topic in topics:
        logger.info(f"\nGenerating script for: {topic.title}")
        script = writer.generate(
            topic=topic,
            video_length=video_length,
            tone=tone,
            niche=niche,
        )
        researcher.mark_used(topic)
        results.append((topic, script))

        logger.info(f"  Title: {script.title}")
        logger.info(f"  Scenes: {len(script.scenes)}")
        logger.info(f"  Duration: ~{script.total_duration/60:.1f} min")

    return results


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    parser = argparse.ArgumentParser(description="Topic Researcher + Script Writer")
    parser.add_argument("--niche", default="ai_tools", choices=list(NICHE_CONFIG.keys()))
    parser.add_argument("--discover", type=int, help="Discover N topics")
    parser.add_argument("--write", type=str, help="Write script for a topic string")
    parser.add_argument("--length", type=int, default=10, help="Video length in minutes")
    parser.add_argument("--tone", default="engaging_serious", choices=list(ScriptWriter.TONE_PROMPTS.keys()))
    parser.add_argument("--provider", default="anthropic", choices=["anthropic", "openai", "gemini"])
    parser.add_argument("--pipeline", type=int, help="Full pipeline: discover + write N videos")
    parser.add_argument("--output", type=str, help="Save script JSON to file")
    args = parser.parse_args()

    if args.discover:
        researcher = TopicResearcher(niche=args.niche)
        topics = researcher.discover(count=args.discover)
        print(f"\n{'='*60}")
        print(f"Top {len(topics)} Topics for '{args.niche}':")
        print(f"{'='*60}")
        for i, t in enumerate(topics, 1):
            print(f"\n{i}. [{t.score:.0f}/100] {t.title}")
            print(f"   Source: {t.source} | Trend: {t.search_volume_trend}")
            if t.related_keywords:
                print(f"   Related: {', '.join(t.related_keywords[:5])}")

    elif args.write:
        writer = ScriptWriter(provider=args.provider)
        script = writer.generate(
            topic=args.write, video_length=args.length,
            tone=args.tone, niche=args.niche,
        )
        print(f"\n{'='*60}")
        print(f"SCRIPT: {script.title}")
        print(f"Duration: ~{script.total_duration/60:.1f} min | Words: {script.total_words}")
        print(f"{'='*60}")
        for scene in script.scenes:
            print(f"\n[Scene {scene.scene_id} — {scene.scene_type.upper()}]")
            print(f"  {scene.narration[:200]}...")
            print(f"  Visual: {scene.visual_cue[:100]}")
        print(f"\nThumbnail idea: {script.thumbnail_concept}")
        print(f"Tags: {', '.join(script.tags[:10])}")

        if args.output:
            with open(args.output, "w") as f:
                json.dump({
                    "title": script.title,
                    "scenes": [asdict(s) for s in script.scenes],
                    "tags": script.tags,
                    "thumbnail_concept": script.thumbnail_concept,
                    "description_seo": script.description_seo,
                }, f, indent=2)
            print(f"\nSaved to {args.output}")

    elif args.pipeline:
        results = discover_and_write(
            niche=args.niche, provider=args.provider,
            video_length=args.length, tone=args.tone,
            count=args.pipeline,
        )
        print(f"\n{'='*60}")
        print(f"Pipeline Complete: {len(results)} videos ready")
        for topic, script in results:
            print(f"\n  Topic: {topic.title}")
            print(f"  Script: {script.title} ({len(script.scenes)} scenes, ~{script.total_duration/60:.1f} min)")

    else:
        parser.print_help()
