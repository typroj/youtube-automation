"""
ANALYTICS TRACKER — Instagram Reel Performance + Weighted Niche Selection
=========================================================================
Uses instagrapi (same library used for posting) to pull your reel view/
engagement data directly from Instagram — no Facebook Page, no Graph API,
no extra permissions needed.

Flow:
  1. refresh_instagram_analytics()  — logs into IG, pulls reel stats, stores in DB
  2. get_niche_weights()             — returns per-niche performance weights
  3. weighted_niche_pick()           — picks a niche proportional to avg views
  4. boost_topics_by_niche()         — blends niche weight into topic scores

Env vars (already in .env):
  INSTAGRAM_USERNAME   — your IG handle
  INSTAGRAM_PASSWORD   — your IG password

Usage:
  # Refresh analytics (fetches latest reel stats)
  python analytics_tracker.py --refresh

  # Show niche performance report
  python analytics_tracker.py --report

  # Pick a niche weighted by performance
  python analytics_tracker.py --pick ai_tools finance tech_gadgets world_crisis
"""

import os
import re
import time
import random
import sqlite3
import logging
import datetime
import argparse
from typing import List, Dict, Optional

logger = logging.getLogger("AnalyticsTracker")

# ═══════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════

DB_TABLE       = "niche_performance"
PLATFORM       = "instagram"
BASELINE_WEIGHT = 100.0   # weight for niches with no data yet
MAX_REELS      = 50       # how many recent reels to fetch per refresh


# ═══════════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════════

def _init_db(db_path: str):
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {DB_TABLE} (
            platform    TEXT NOT NULL,
            media_id    TEXT NOT NULL,
            niche       TEXT NOT NULL,
            views       INTEGER DEFAULT 0,
            likes       INTEGER DEFAULT 0,
            comments    INTEGER DEFAULT 0,
            caption     TEXT,
            posted_at   TEXT,
            fetched_at  TEXT,
            PRIMARY KEY (platform, media_id)
        )
    """)
    conn.commit()
    conn.close()


def _upsert_reel(db_path: str, media_id: str, niche: str,
                 views: int, likes: int, comments: int,
                 caption: str, posted_at: str):
    conn = sqlite3.connect(db_path)
    conn.execute(f"""
        INSERT INTO {DB_TABLE}
            (platform, media_id, niche, views, likes, comments,
             caption, posted_at, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(platform, media_id) DO UPDATE SET
            views=excluded.views, likes=excluded.likes,
            comments=excluded.comments,
            fetched_at=excluded.fetched_at
    """, (PLATFORM, str(media_id), niche, views, likes, comments,
          (caption or "")[:300], posted_at,
          datetime.datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════
#  NICHE WEIGHTS
# ═══════════════════════════════════════════════════════════════

def get_niche_weights(db_path: str, niches: List[str]) -> Dict[str, float]:
    """
    Return {niche: weight} for all requested niches.
    Weight = avg_views × (1 + engagement_boost).
    Niches with no data get BASELINE_WEIGHT.
    """
    _init_db(db_path)
    conn = sqlite3.connect(db_path)
    weights = {}
    for niche in niches:
        rows = conn.execute(f"""
            SELECT views, likes, comments FROM {DB_TABLE}
            WHERE platform=? AND niche=? AND views > 0
        """, (PLATFORM, niche)).fetchall()

        if not rows:
            weights[niche] = BASELINE_WEIGHT
            continue

        total_views  = sum(r[0] for r in rows)
        total_engage = sum(r[1] + r[2] for r in rows)
        avg_views    = total_views / len(rows)
        engage_boost = min(total_engage / max(total_views, 1), 0.5)
        weights[niche] = round(avg_views * (1 + engage_boost), 1)

    conn.close()
    return weights


def weighted_niche_pick(db_path: str, niches: List[str]) -> str:
    """Pick one niche from the list, weighted by average Instagram reel views."""
    weights     = get_niche_weights(db_path, niches)
    niche_list  = list(weights.keys())
    weight_list = [weights[n] for n in niche_list]
    picked = random.choices(niche_list, weights=weight_list, k=1)[0]
    logger.info(
        f"  Weighted niche pick → {picked}  "
        f"({', '.join(f'{n}={w:.0f}' for n, w in weights.items())})"
    )
    return picked


def boost_topics_by_niche(topics, niche: str, db_path: str):
    """
    Multiply each topic's score by a factor derived from the niche's
    Instagram performance.  Better-performing niches push topics higher.
    """
    weights   = get_niche_weights(db_path, [niche])
    niche_w   = weights.get(niche, BASELINE_WEIGHT)
    boost     = min((niche_w / BASELINE_WEIGHT - 1.0) * 0.3, 1.0)
    boost     = max(boost, 0.0)
    for t in topics:
        t.score = round(t.score * (1 + boost), 2)
    return topics


# ═══════════════════════════════════════════════════════════════
#  NICHE TAGGING
# ═══════════════════════════════════════════════════════════════

def _tag_niche(caption: str, niche_config: dict) -> str:
    """Map a reel caption to the best-matching niche by keyword count."""
    text   = (caption or "").lower()
    scores = {}
    for niche, cfg in niche_config.items():
        hits = sum(1 for s in cfg.get("seeds", []) if s.lower() in text)
        if hits:
            scores[niche] = hits
    return max(scores, key=scores.get) if scores else "unknown"


# ═══════════════════════════════════════════════════════════════
#  INSTAGRAM (via instagrapi)
# ═══════════════════════════════════════════════════════════════

def _login(username: str, password: str):
    """Login to Instagram using instagrapi and return the client."""
    from instagrapi import Client
    session_file = f"data/ig_session_{username}.json"
    cl = Client()
    cl.delay_range = [1, 3]

    if os.path.exists(session_file):
        try:
            cl.load_settings(session_file)
            cl.login(username, password)
            logger.info("  Instagram: session restored")
            return cl
        except Exception:
            logger.warning("  Instagram: session expired, re-logging in…")

    cl.login(username, password)
    os.makedirs("data", exist_ok=True)
    cl.dump_settings(session_file)
    logger.info("  Instagram: logged in, session saved")
    return cl


def refresh_instagram_analytics(db_path: str, niche_config: dict,
                                 username: str = "",
                                 password: str = "",
                                 max_reels: int = MAX_REELS) -> int:
    """
    Login to Instagram, fetch recent reels with insights, tag to niches,
    and store in the performance DB.

    Returns the number of reels processed.
    """
    _init_db(db_path)

    username = username or os.getenv("INSTAGRAM_USERNAME", "")
    password = password or os.getenv("INSTAGRAM_PASSWORD", "")

    if not username or not password:
        raise ValueError("INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD must be set in .env")

    # ── Login ──────────────────────────────────────────────────
    cl = _login(username, password)

    # ── Get own user ID ────────────────────────────────────────
    user_id = cl.user_id_from_username(username)
    logger.info(f"  Fetching up to {max_reels} reels for @{username} (id={user_id})…")

    # ── Fetch recent media ─────────────────────────────────────
    medias = cl.user_medias(user_id, amount=max_reels)
    reels  = [m for m in medias if str(m.media_type) in ("2", "VIDEO") or m.product_type == "clips"]

    if not reels:
        # Fallback: all video-type media if no reels flagged
        reels = [m for m in medias if m.media_type == 2]

    logger.info(f"  Found {len(reels)} reels out of {len(medias)} total posts")

    # ── Process each reel ─────────────────────────────────────
    processed = 0
    for media in reels:
        try:
            caption   = media.caption_text or ""
            posted_at = media.taken_at.isoformat() if media.taken_at else ""
            likes     = media.like_count or 0
            comments  = media.comment_count or 0

            # View count: use play_count or view_count depending on media type
            views = getattr(media, "play_count", None) or getattr(media, "view_count", None) or 0

            # If still 0, try fetching insights (requires business account)
            if views == 0:
                try:
                    insights = cl.media_insights(media.pk)
                    views    = insights.get("plays", 0) or insights.get("video_views", 0) or 0
                except Exception:
                    pass   # personal accounts can't access insights; use 0

            niche = _tag_niche(caption, niche_config)
            _upsert_reel(db_path, str(media.pk), niche, views, likes, comments,
                         caption, posted_at)

            logger.info(
                f"  [{processed+1:>3}/{len(reels)}] {niche:<22} "
                f"views={views:>6}  likes={likes:>5}  "
                f"{caption[:45].strip()!r}"
            )
            processed += 1
            time.sleep(0.5)

        except Exception as e:
            logger.warning(f"  Skipped media {media.pk}: {e}")

    logger.info(f"  Analytics refresh complete — {processed} reels stored")
    return processed


# ═══════════════════════════════════════════════════════════════
#  REPORT
# ═══════════════════════════════════════════════════════════════

def print_performance_report(db_path: str):
    """Print a formatted niche performance table to stdout."""
    _init_db(db_path)
    conn = sqlite3.connect(db_path)
    rows = conn.execute(f"""
        SELECT niche,
               COUNT(*)       AS reels,
               AVG(views)     AS avg_views,
               SUM(views)     AS total_views,
               MAX(views)     AS best_reel,
               AVG(likes)     AS avg_likes
        FROM {DB_TABLE}
        WHERE platform=?
        GROUP BY niche
        ORDER BY avg_views DESC
    """, (PLATFORM,)).fetchall()
    conn.close()

    if not rows:
        print("  No analytics data yet — run:  python analytics_tracker.py --refresh")
        return

    print(f"\n{'='*72}")
    print(f"  INSTAGRAM REEL PERFORMANCE")
    print(f"{'='*72}")
    print(f"  {'Niche':<22} {'Reels':>6} {'Avg Views':>10} {'Total Views':>12} {'Best':>8}")
    print(f"  {'-'*62}")
    for niche, reels, avg_v, total_v, best, avg_l in rows:
        print(f"  {niche:<22} {reels:>6} {avg_v:>10.0f} {total_v:>12} {best:>8}")
    print(f"{'='*72}\n")


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    from topic_and_script import NICHE_CONFIG

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)-18s] %(message)s",
        handlers=[logging.StreamHandler()],
    )

    parser = argparse.ArgumentParser(description="Instagram Analytics Tracker")
    parser.add_argument("--refresh", action="store_true",
                        help="Pull latest reel stats from Instagram")
    parser.add_argument("--report",  action="store_true",
                        help="Show niche performance report")
    parser.add_argument("--pick", nargs="+", metavar="NICHE",
                        help="Pick a niche weighted by performance")
    parser.add_argument("--db", default="data/topics.db")
    args = parser.parse_args()

    if args.refresh:
        n = refresh_instagram_analytics(args.db, NICHE_CONFIG)
        print(f"\n  Refreshed {n} reels.")

    if args.report or not any([args.refresh, args.pick]):
        print_performance_report(args.db)

    if args.pick:
        picked = weighted_niche_pick(args.db, args.pick)
        print(f"\n  Weighted pick → {picked}\n")