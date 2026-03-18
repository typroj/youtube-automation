"""
YOUTUBE UPLOADER — Faceless YouTube Automation Pipeline
========================================================
Production-ready module for automated YouTube video uploading with:
  - OAuth 2.0 authentication (headless server-friendly, token refresh)
  - Resumable uploads with exponential backoff
  - Custom thumbnail upload
  - Scheduled publishing (publishAt)
  - AI-generated SEO metadata (title, description, tags, hashtags)
  - Playlist management (auto-add to playlist)
  - Upload history & quota tracking
  - Slack/Telegram notifications
  - Analytics fetching (post-upload performance)

API Quota: YouTube Data API v3 gives 10,000 units/day.
  - Video upload (videos.insert) = 1,600 units
  - Thumbnail upload (thumbnails.set) = 50 units
  - Playlist item insert = 50 units
  - Videos.update (metadata) = 50 units
  → You can upload ~6 videos/day with metadata + thumbnails

Dependencies:
    pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
    pip install anthropic openai requests python-dotenv

Setup:
    1. Go to https://console.cloud.google.com/
    2. Create project → Enable "YouTube Data API v3"
    3. Create OAuth 2.0 credentials (Desktop app type)
    4. Download client_secret.json
    5. Run first-time auth: python youtube_uploader.py --setup
"""

import os
import sys
import json
import time
import random
import logging
import datetime
import pickle
import sqlite3
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field, asdict

import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# ─── Configuration ────────────────────────────────────────────────────────────

@dataclass
class UploaderConfig:
    """Master configuration for the YouTube uploader."""

    # OAuth paths
    client_secret_path: str = "config/client_secret.json"
    token_pickle_path: str = "config/youtube_token.pickle"

    # API settings
    api_service_name: str = "youtube"
    api_version: str = "v3"
    scopes: List[str] = field(default_factory=lambda: [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.force-ssl",
    ])

    # Upload settings
    chunk_size: int = -1            # -1 = single request; use 1048576 (1MB) for chunked
    max_retries: int = 10
    default_privacy: str = "private"  # private → review → publish (safer for automation)
    default_category_id: str = "28"   # 28=Science & Tech, 22=People & Blogs, 27=Education
    default_language: str = "en"
    made_for_kids: bool = False
    notify_subscribers: bool = True

    # Quota tracking
    daily_quota_limit: int = 10000
    upload_quota_cost: int = 1600    # per video upload
    thumbnail_quota_cost: int = 50
    playlist_quota_cost: int = 50
    db_path: str = "data/upload_history.db"

    # Notifications
    slack_webhook_url: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # SEO
    max_title_length: int = 100
    max_description_length: int = 5000
    max_tags: int = 30
    max_tag_length: int = 500       # total chars for all tags combined


@dataclass
class VideoMetadata:
    """Metadata for a single video upload."""
    title: str
    description: str
    tags: List[str] = field(default_factory=list)
    category_id: str = "28"
    privacy_status: str = "private"
    publish_at: Optional[str] = None       # ISO 8601: "2026-03-15T14:00:00.000Z"
    language: str = "en"
    playlist_id: Optional[str] = None
    made_for_kids: bool = False
    thumbnail_path: Optional[str] = None

    # Populated after upload
    video_id: Optional[str] = None
    video_url: Optional[str] = None
    upload_timestamp: Optional[str] = None


# ─── Retriable Error Handling ─────────────────────────────────────────────────

RETRIABLE_STATUS_CODES = [500, 502, 503, 504]
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError)


# ─── Core YouTube Uploader ────────────────────────────────────────────────────

class YouTubeUploader:
    """
    Production-ready YouTube video uploader with full automation support.

    Usage:
        uploader = YouTubeUploader()
        uploader.authenticate()

        metadata = VideoMetadata(
            title="How AI is Changing Everything in 2026",
            description="In this video we explore...",
            tags=["AI", "technology", "2026"],
            privacy_status="private",
            publish_at="2026-03-20T14:00:00.000Z",
            thumbnail_path="output/thumbnail.jpg",
            playlist_id="PLxxxxx",
        )

        result = uploader.upload(
            video_path="output/final_video.mp4",
            metadata=metadata,
        )
        print(f"Uploaded: {result.video_url}")
    """

    def __init__(self, config: UploaderConfig = None):
        self.config = config or UploaderConfig()
        self.youtube = None
        self.credentials = None
        self.logger = logging.getLogger("YouTubeUploader")
        self._init_db()

    # ─── AUTHENTICATION ───────────────────────────────────────────────────

    def authenticate(self) -> bool:
        """
        Authenticate with YouTube using OAuth 2.0.

        Flow:
            1. Check for saved token pickle
            2. If token exists and is valid → use it
            3. If token expired → refresh it
            4. If no token → run OAuth flow (first-time only, needs browser)

        For headless servers (no browser):
            - Run --setup on local machine first
            - Copy the token pickle to server
            - Token auto-refreshes from there
        """
        creds = None
        token_path = self.config.token_pickle_path

        # Step 1: Load saved credentials
        if os.path.exists(token_path):
            with open(token_path, "rb") as token_file:
                creds = pickle.load(token_file)
            self.logger.info("Loaded saved credentials")

        # Step 2: Refresh or run new auth flow
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    self.logger.info("Refreshed expired credentials")
                except Exception as e:
                    self.logger.warning(f"Token refresh failed: {e}")
                    creds = self._run_oauth_flow()
            else:
                creds = self._run_oauth_flow()

            # Save refreshed/new credentials
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, "wb") as token_file:
                pickle.dump(creds, token_file)
            self.logger.info(f"Credentials saved to {token_path}")

        self.credentials = creds
        self.youtube = build(
            self.config.api_service_name,
            self.config.api_version,
            credentials=creds,
        )
        self.logger.info("YouTube API client authenticated successfully")
        return True

    def _run_oauth_flow(self) -> Credentials:
        """
        Run the OAuth 2.0 authorization flow.
        
        First-time setup requires a browser. After that, the token
        auto-refreshes on the server.
        """
        secret_path = self.config.client_secret_path
        if not os.path.exists(secret_path):
            raise FileNotFoundError(
                f"client_secret.json not found at {secret_path}.\n"
                "Download it from: https://console.cloud.google.com/apis/credentials\n"
                "Place it in the config/ directory."
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            secret_path,
            scopes=self.config.scopes,
        )

        # Try local server first, fall back to console for headless
        try:
            creds = flow.run_local_server(
                port=8090,
                prompt="consent",
                access_type="offline",  # CRITICAL: gets refresh_token
            )
        except Exception:
            self.logger.info("Browser auth unavailable, using console flow...")
            creds = flow.run_console()

        return creds

    # ─── VIDEO UPLOAD ─────────────────────────────────────────────────────

    def upload(
        self,
        video_path: str,
        metadata: VideoMetadata,
    ) -> VideoMetadata:
        """
        Upload a video to YouTube with full metadata, thumbnail, and playlist.

        Steps:
            1. Check quota availability
            2. Validate metadata
            3. Upload video (resumable)
            4. Upload thumbnail (if provided)
            5. Add to playlist (if specified)
            6. Record in upload history
            7. Send notification

        Returns:
            Updated VideoMetadata with video_id and video_url populated
        """
        if not self.youtube:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        # Step 1: Check quota
        quota_used = self._get_today_quota_usage()
        quota_needed = self.config.upload_quota_cost
        if metadata.thumbnail_path:
            quota_needed += self.config.thumbnail_quota_cost
        if metadata.playlist_id:
            quota_needed += self.config.playlist_quota_cost

        if quota_used + quota_needed > self.config.daily_quota_limit:
            remaining = self.config.daily_quota_limit - quota_used
            raise QuotaExceededError(
                f"Daily quota would be exceeded. "
                f"Used: {quota_used}, Needed: {quota_needed}, Remaining: {remaining}. "
                f"Try again after midnight Pacific Time."
            )

        # Step 2: Validate metadata
        metadata = self._validate_metadata(metadata)

        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        self.logger.info(
            f"Uploading: '{metadata.title}' ({file_size_mb:.1f} MB) "
            f"[{metadata.privacy_status}]"
        )

        # Step 3: Upload video
        body = self._build_request_body(metadata)

        media = MediaFileUpload(
            video_path,
            chunksize=self.config.chunk_size,
            resumable=True,
            mimetype="video/*",
        )

        insert_request = self.youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
            notifySubscribers=self.config.notify_subscribers,
        )

        video_id = self._resumable_upload(insert_request)

        if not video_id:
            raise UploadError("Upload failed after all retries")

        metadata.video_id = video_id
        metadata.video_url = f"https://www.youtube.com/watch?v={video_id}"
        metadata.upload_timestamp = datetime.datetime.utcnow().isoformat()

        self.logger.info(f"Video uploaded: {metadata.video_url}")

        # Step 4: Upload thumbnail
        if metadata.thumbnail_path and os.path.exists(metadata.thumbnail_path):
            self._upload_thumbnail(video_id, metadata.thumbnail_path)

        # Step 5: Add to playlist
        if metadata.playlist_id:
            self._add_to_playlist(video_id, metadata.playlist_id)

        # Step 6: Record history
        self._record_upload(metadata, video_path, quota_needed)

        # Step 7: Notify
        self._send_notification(metadata, file_size_mb)

        return metadata

    def _build_request_body(self, metadata: VideoMetadata) -> Dict:
        """Build the API request body from VideoMetadata."""
        body = {
            "snippet": {
                "title": metadata.title[:self.config.max_title_length],
                "description": metadata.description[:self.config.max_description_length],
                "tags": metadata.tags[:self.config.max_tags],
                "categoryId": metadata.category_id,
                "defaultLanguage": metadata.language,
                "defaultAudioLanguage": metadata.language,
            },
            "status": {
                "privacyStatus": metadata.privacy_status,
                "selfDeclaredMadeForKids": metadata.made_for_kids,
                "embeddable": True,
                "license": "youtube",
            },
        }

        # Scheduled publishing: set privacy to "private" and add publishAt
        if metadata.publish_at:
            body["status"]["privacyStatus"] = "private"
            body["status"]["publishAt"] = metadata.publish_at

        return body

    def _resumable_upload(self, request) -> Optional[str]:
        """
        Execute a resumable upload with exponential backoff retry.

        Returns video_id on success, None on failure.

        The exponential backoff pattern:
            retry 1: wait 0–2s
            retry 2: wait 0–4s
            retry 3: wait 0–8s
            ...up to max_retries
        """
        response = None
        error = None
        retry = 0

        while response is None:
            try:
                self.logger.info("Uploading...")
                status, response = request.next_chunk()

                if status:
                    progress = int(status.progress() * 100)
                    self.logger.info(f"  Upload progress: {progress}%")

                if response is not None:
                    if "id" in response:
                        return response["id"]
                    else:
                        raise UploadError(
                            f"Upload finished but no video ID in response: {response}"
                        )

            except HttpError as e:
                if e.resp.status in RETRIABLE_STATUS_CODES:
                    error = f"Retriable HTTP {e.resp.status}: {e.content.decode()[:200]}"
                else:
                    raise UploadError(f"Non-retriable HTTP error {e.resp.status}: {e.content.decode()[:500]}")

            except RETRIABLE_EXCEPTIONS as e:
                error = f"Retriable error: {e}"

            if error:
                retry += 1
                if retry > self.config.max_retries:
                    self.logger.error(f"Upload failed after {self.config.max_retries} retries")
                    return None

                wait = random.random() * (2 ** retry)
                self.logger.warning(f"  {error}")
                self.logger.info(f"  Retry {retry}/{self.config.max_retries} in {wait:.1f}s...")
                time.sleep(wait)
                error = None

        return None

    # ─── THUMBNAIL UPLOAD ─────────────────────────────────────────────────

    def _upload_thumbnail(self, video_id: str, thumbnail_path: str):
        """
        Upload a custom thumbnail for a video.

        Requirements:
            - YouTube account must be VERIFIED (phone verification)
            - Image: JPEG/PNG, <2MB, 1280x720 recommended
        """
        try:
            self.youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(
                    thumbnail_path,
                    mimetype="image/jpeg" if thumbnail_path.endswith(".jpg") else "image/png",
                ),
            ).execute()
            self.logger.info(f"  Thumbnail uploaded for {video_id}")
        except HttpError as e:
            # Common error: account not verified
            self.logger.warning(
                f"  Thumbnail upload failed: {e.resp.status}. "
                f"Ensure your YouTube account is verified at https://www.youtube.com/verify"
            )

    # ─── PLAYLIST MANAGEMENT ──────────────────────────────────────────────

    def _add_to_playlist(self, video_id: str, playlist_id: str):
        """Add the uploaded video to a specified playlist."""
        try:
            self.youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {
                            "kind": "youtube#video",
                            "videoId": video_id,
                        },
                    }
                },
            ).execute()
            self.logger.info(f"  Added to playlist {playlist_id}")
        except HttpError as e:
            self.logger.warning(f"  Playlist insert failed: {e.resp.status}")

    def create_playlist(self, title: str, description: str = "", privacy: str = "public") -> str:
        """Create a new playlist and return its ID."""
        response = self.youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {"title": title, "description": description},
                "status": {"privacyStatus": privacy},
            },
        ).execute()
        playlist_id = response["id"]
        self.logger.info(f"  Created playlist '{title}': {playlist_id}")
        return playlist_id

    # ─── UPDATE PUBLISHED VIDEO ───────────────────────────────────────────

    def update_metadata(self, video_id: str, updates: Dict) -> Dict:
        """
        Update metadata of an already-uploaded video.

        Args:
            video_id: YouTube video ID
            updates: Dict with keys like "title", "description", "tags",
                     "privacy_status", "category_id"

        Useful for:
            - A/B testing titles
            - Changing privacy from private → public after review
            - Updating description with affiliate links
        """
        # Fetch current metadata
        current = self.youtube.videos().list(
            part="snippet,status",
            id=video_id,
        ).execute()

        if not current.get("items"):
            raise ValueError(f"Video {video_id} not found")

        video = current["items"][0]
        snippet = video["snippet"]
        status = video["status"]

        # Apply updates
        if "title" in updates:
            snippet["title"] = updates["title"]
        if "description" in updates:
            snippet["description"] = updates["description"]
        if "tags" in updates:
            snippet["tags"] = updates["tags"]
        if "category_id" in updates:
            snippet["categoryId"] = updates["category_id"]
        if "privacy_status" in updates:
            status["privacyStatus"] = updates["privacy_status"]

        response = self.youtube.videos().update(
            part="snippet,status",
            body={
                "id": video_id,
                "snippet": snippet,
                "status": status,
            },
        ).execute()

        self.logger.info(f"  Updated metadata for {video_id}")
        return response

    def publish_video(self, video_id: str):
        """Change a private/unlisted video to public."""
        return self.update_metadata(video_id, {"privacy_status": "public"})

    # ─── METADATA VALIDATION ──────────────────────────────────────────────

    def _validate_metadata(self, metadata: VideoMetadata) -> VideoMetadata:
        """Validate and sanitise video metadata before upload."""

        # Title
        if not metadata.title or not metadata.title.strip():
            raise ValueError("Video title cannot be empty")
        metadata.title = metadata.title.strip()[:self.config.max_title_length]

        # YouTube blocks certain characters in titles
        for char in ["<", ">"]:
            metadata.title = metadata.title.replace(char, "")

        # Description
        if metadata.description:
            metadata.description = metadata.description[:self.config.max_description_length]

        # Tags — remove empty, duplicates, and trim
        if metadata.tags:
            seen = set()
            clean_tags = []
            total_chars = 0
            for tag in metadata.tags:
                tag = tag.strip()
                lower = tag.lower()
                if tag and lower not in seen:
                    if total_chars + len(tag) <= self.config.max_tag_length:
                        clean_tags.append(tag)
                        seen.add(lower)
                        total_chars += len(tag)
            metadata.tags = clean_tags[:self.config.max_tags]

        # Category
        if not metadata.category_id:
            metadata.category_id = self.config.default_category_id

        # Privacy
        valid_privacy = ("public", "private", "unlisted")
        if metadata.privacy_status not in valid_privacy:
            metadata.privacy_status = self.config.default_privacy

        # Publish date validation
        if metadata.publish_at:
            try:
                dt = datetime.datetime.fromisoformat(
                    metadata.publish_at.replace("Z", "+00:00")
                )
                if dt < datetime.datetime.now(datetime.timezone.utc):
                    self.logger.warning("publish_at is in the past — publishing immediately")
                    metadata.publish_at = None
            except ValueError:
                self.logger.warning(f"Invalid publish_at format: {metadata.publish_at}")
                metadata.publish_at = None

        return metadata

    # ─── QUOTA TRACKING ───────────────────────────────────────────────────

    def _init_db(self):
        """Initialize SQLite database for upload history and quota tracking."""
        os.makedirs(os.path.dirname(self.config.db_path), exist_ok=True)
        conn = sqlite3.connect(self.config.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT,
                title TEXT,
                video_path TEXT,
                thumbnail_path TEXT,
                privacy_status TEXT,
                publish_at TEXT,
                playlist_id TEXT,
                quota_used INTEGER,
                upload_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quota_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                units_used INTEGER,
                operation TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def _get_today_quota_usage(self) -> int:
        """Get total quota units used today (resets at midnight Pacific Time)."""
        today = datetime.date.today().isoformat()
        conn = sqlite3.connect(self.config.db_path)
        result = conn.execute(
            "SELECT COALESCE(SUM(units_used), 0) FROM quota_log WHERE date = ?",
            (today,)
        ).fetchone()
        conn.close()
        return result[0]

    def _record_upload(self, metadata: VideoMetadata, video_path: str, quota_used: int):
        """Record upload in history database."""
        today = datetime.date.today().isoformat()
        conn = sqlite3.connect(self.config.db_path)
        conn.execute(
            """INSERT INTO uploads 
               (video_id, title, video_path, thumbnail_path, privacy_status,
                publish_at, playlist_id, quota_used, upload_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (metadata.video_id, metadata.title, video_path, metadata.thumbnail_path,
             metadata.privacy_status, metadata.publish_at, metadata.playlist_id,
             quota_used, today)
        )
        conn.execute(
            "INSERT INTO quota_log (date, units_used, operation) VALUES (?, ?, ?)",
            (today, quota_used, "upload+thumbnail+playlist")
        )
        conn.commit()
        conn.close()

    def get_upload_history(self, limit: int = 20) -> List[Dict]:
        """Retrieve recent upload history."""
        conn = sqlite3.connect(self.config.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM uploads ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_quota_status(self) -> Dict:
        """Get current daily quota status."""
        used = self._get_today_quota_usage()
        remaining = self.config.daily_quota_limit - used
        max_uploads = remaining // (self.config.upload_quota_cost + self.config.thumbnail_quota_cost)
        return {
            "date": datetime.date.today().isoformat(),
            "used": used,
            "remaining": remaining,
            "limit": self.config.daily_quota_limit,
            "max_uploads_remaining": max_uploads,
        }

    # ─── NOTIFICATIONS ────────────────────────────────────────────────────

    def _send_notification(self, metadata: VideoMetadata, file_size_mb: float):
        """Send upload notification via Slack and/or Telegram."""
        message = (
            f"{'=' * 40}\n"
            f"YouTube Upload Complete\n"
            f"{'=' * 40}\n"
            f"Title: {metadata.title}\n"
            f"URL: {metadata.video_url}\n"
            f"Status: {metadata.privacy_status}\n"
            f"Size: {file_size_mb:.1f} MB\n"
        )
        if metadata.publish_at:
            message += f"Scheduled: {metadata.publish_at}\n"

        # Slack
        if self.config.slack_webhook_url:
            try:
                import requests
                requests.post(
                    self.config.slack_webhook_url,
                    json={"text": message},
                    timeout=10,
                )
                self.logger.info("  Slack notification sent")
            except Exception as e:
                self.logger.warning(f"  Slack notification failed: {e}")

        # Telegram
        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            try:
                import requests
                requests.post(
                    f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": self.config.telegram_chat_id,
                        "text": message,
                    },
                    timeout=10,
                )
                self.logger.info("  Telegram notification sent")
            except Exception as e:
                self.logger.warning(f"  Telegram notification failed: {e}")

    # ─── ANALYTICS ────────────────────────────────────────────────────────

    def get_video_stats(self, video_id: str) -> Dict:
        """
        Fetch basic statistics for an uploaded video.
        
        Useful for post-upload monitoring: check views, likes, comments
        24-48 hours after publishing to gauge performance.
        """
        response = self.youtube.videos().list(
            part="statistics,snippet",
            id=video_id,
        ).execute()

        if not response.get("items"):
            return {"error": f"Video {video_id} not found"}

        item = response["items"][0]
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})

        return {
            "video_id": video_id,
            "title": snippet.get("title"),
            "published_at": snippet.get("publishedAt"),
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
            "favorite_count": int(stats.get("favoriteCount", 0)),
        }

    def get_channel_stats(self) -> Dict:
        """Fetch basic channel statistics."""
        response = self.youtube.channels().list(
            part="statistics,snippet",
            mine=True,
        ).execute()

        if not response.get("items"):
            return {"error": "Channel not found"}

        item = response["items"][0]
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})

        return {
            "channel_name": snippet.get("title"),
            "subscriber_count": int(stats.get("subscriberCount", 0)),
            "video_count": int(stats.get("videoCount", 0)),
            "view_count": int(stats.get("viewCount", 0)),
        }


# ─── SEO METADATA GENERATOR ──────────────────────────────────────────────────

class SEOMetadataGenerator:
    """
    Generate optimised YouTube metadata (title, description, tags) using an LLM.
    
    Usage:
        seo = SEOMetadataGenerator(provider="anthropic")  # or "openai"
        metadata = seo.generate(
            topic="How AI is changing content creation in 2026",
            script_summary="This video covers...",
            niche="ai_tools",
            target_audience="tech-savvy creators aged 18-35",
        )
    """

    # YouTube category IDs for common niches
    CATEGORY_MAP = {
        "ai_tools": "28",          # Science & Technology
        "finance": "22",           # People & Blogs (no Finance category)
        "education": "27",         # Education
        "entertainment": "24",     # Entertainment
        "gaming": "20",            # Gaming
        "health": "26",            # Howto & Style
        "music": "10",             # Music
        "news": "25",              # News & Politics
        "tech": "28",              # Science & Technology
        "self_improvement": "22",  # People & Blogs
        "history": "27",           # Education
    }

    def __init__(self, provider: str = "anthropic", api_key: Optional[str] = None):
        self.provider = provider
        self.api_key = api_key or os.environ.get(
            "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
        )

    def generate(
        self,
        topic: str,
        script_summary: str = "",
        niche: str = "tech",
        target_audience: str = "",
    ) -> VideoMetadata:
        """
        Generate SEO-optimised YouTube metadata from a topic.
        
        Returns VideoMetadata with title, description, tags, and category.
        """
        prompt = self._build_prompt(topic, script_summary, niche, target_audience)

        if self.provider == "anthropic":
            result = self._call_anthropic(prompt)
        else:
            result = self._call_openai(prompt)

        # Parse JSON response
        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code block
            if "```json" in result:
                json_str = result.split("```json")[1].split("```")[0].strip()
                data = json.loads(json_str)
            elif "```" in result:
                json_str = result.split("```")[1].split("```")[0].strip()
                data = json.loads(json_str)
            else:
                raise ValueError(f"Could not parse SEO metadata from LLM response: {result[:500]}")

        category_id = self.CATEGORY_MAP.get(niche, "28")

        return VideoMetadata(
            title=data.get("title", topic)[:100],
            description=self._build_description(data),
            tags=data.get("tags", [])[:30],
            category_id=category_id,
        )

    def _build_prompt(self, topic, summary, niche, audience) -> str:
        return f"""You are a YouTube SEO expert. Generate optimised metadata for a faceless YouTube video.

Topic: {topic}
Script Summary: {summary[:500] if summary else 'N/A'}
Niche: {niche}
Target Audience: {audience or 'General'}

Return ONLY a JSON object (no markdown, no explanation) with these keys:
{{
  "title": "Compelling title under 70 chars with primary keyword near the start",
  "description_intro": "First 2 sentences (most important for SEO, appears in search)",
  "description_body": "Detailed 3-5 sentence paragraph about the content",
  "timestamps": ["0:00 Introduction", "1:30 Key Point 1", ...],
  "tags": ["tag1", "tag2", ...],
  "hashtags": ["#hashtag1", "#hashtag2", "#hashtag3"]
}}

Rules:
- Title: 50-70 chars, include primary keyword, create curiosity gap
- Tags: 15-25 tags mixing broad and specific keywords
- Hashtags: exactly 3 (YouTube shows first 3 above the title)
- Description intro: include primary keyword in first sentence
- Timestamps: estimate 5-8 timestamps for a 10-minute video"""

    def _build_description(self, data: Dict) -> str:
        """Assemble the full YouTube description from generated parts."""
        parts = []

        # Intro (most important for SEO — appears in search results)
        if data.get("description_intro"):
            parts.append(data["description_intro"])

        parts.append("")  # blank line

        # Body
        if data.get("description_body"):
            parts.append(data["description_body"])

        parts.append("")

        # Timestamps (chapters)
        if data.get("timestamps"):
            parts.append("TIMESTAMPS:")
            for ts in data["timestamps"]:
                parts.append(ts)

        parts.append("")

        # Hashtags
        if data.get("hashtags"):
            parts.append(" ".join(data["hashtags"][:3]))

        parts.append("")

        # Boilerplate (customize this for your channel)
        parts.append("─────────────────────────────────")
        parts.append("Subscribe for more content like this!")
        parts.append("")
        parts.append("DISCLAIMER: This video is for educational and informational purposes only.")

        return "\n".join(parts)

    def _call_anthropic(self, prompt: str) -> str:
        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def _call_openai(self, prompt: str) -> str:
        import openai
        client = openai.OpenAI(api_key=self.api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
        )
        return response.choices[0].message.content


# ─── SCHEDULING HELPER ────────────────────────────────────────────────────────

class PublishScheduler:
    """
    Calculate optimal publish times based on audience timezone and best practices.
    
    YouTube's best publish windows (IST for Indian audience):
        - Weekdays: 2:00 PM – 4:00 PM IST (early afternoon, post-lunch)
        - Weekends: 10:00 AM – 12:00 PM IST (late morning)
    
    For US audience:
        - Weekdays: 12:00 PM – 3:00 PM EST
        - Weekends: 9:00 AM – 11:00 AM EST
    """

    OPTIMAL_HOURS_IST = {
        "weekday": [14, 15, 16, 17],       # 2 PM – 5 PM IST
        "weekend": [10, 11, 12],            # 10 AM – 12 PM IST
    }

    OPTIMAL_HOURS_EST = {
        "weekday": [12, 13, 14, 15],       # 12 PM – 3 PM EST
        "weekend": [9, 10, 11],             # 9 AM – 11 AM EST
    }

    @staticmethod
    def get_next_publish_time(
        timezone: str = "IST",
        days_ahead: int = 1,
    ) -> str:
        """
        Calculate the next optimal publish time.
        
        Returns ISO 8601 timestamp for YouTube's publishAt field.
        
        Args:
            timezone: "IST" or "EST"
            days_ahead: minimum days from now (1 = tomorrow)
        """
        import datetime as dt

        now = dt.datetime.utcnow()
        target_date = now + dt.timedelta(days=days_ahead)

        # Determine weekday/weekend
        is_weekend = target_date.weekday() >= 5

        if timezone == "IST":
            hours = PublishScheduler.OPTIMAL_HOURS_IST
            utc_offset = 5.5  # IST = UTC+5:30
        else:
            hours = PublishScheduler.OPTIMAL_HOURS_EST
            utc_offset = -5   # EST = UTC-5

        slot = "weekend" if is_weekend else "weekday"
        local_hour = random.choice(hours[slot])

        # Convert to UTC
        utc_hour = local_hour - utc_offset
        if utc_hour < 0:
            utc_hour += 24
            target_date -= dt.timedelta(days=1)

        publish_time = target_date.replace(
            hour=int(utc_hour),
            minute=random.choice([0, 15, 30]),
            second=0,
            microsecond=0,
        )

        return publish_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    @staticmethod
    def get_batch_schedule(
        count: int = 7,
        timezone: str = "IST",
        start_days_ahead: int = 1,
    ) -> List[str]:
        """
        Generate a batch of scheduled publish times (e.g., for a week of videos).
        
        Returns list of ISO 8601 timestamps, one per day.
        """
        return [
            PublishScheduler.get_next_publish_time(timezone, start_days_ahead + i)
            for i in range(count)
        ]


# ─── BATCH UPLOADER ──────────────────────────────────────────────────────────

class BatchUploader:
    """
    Upload multiple videos with staggered scheduling.
    
    Usage:
        batch = BatchUploader()
        batch.authenticate()
        
        videos = [
            {"video_path": "vid1.mp4", "topic": "AI Tools 2026", ...},
            {"video_path": "vid2.mp4", "topic": "Best GPUs for AI", ...},
        ]
        
        results = batch.upload_batch(videos, schedule_timezone="IST")
    """

    def __init__(self, config: UploaderConfig = None):
        self.uploader = YouTubeUploader(config)
        self.seo = SEOMetadataGenerator()
        self.scheduler = PublishScheduler()
        self.logger = logging.getLogger("BatchUploader")

    def authenticate(self):
        self.uploader.authenticate()

    def upload_batch(
        self,
        videos: List[Dict],
        schedule_timezone: str = "IST",
        playlist_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> List[VideoMetadata]:
        """
        Upload a batch of videos with auto-generated SEO metadata and scheduling.
        
        Args:
            videos: List of dicts with keys:
                - video_path: str (required)
                - topic: str (for SEO generation)
                - script_summary: str (optional)
                - thumbnail_path: str (optional)
                - title: str (optional, overrides SEO)
                - description: str (optional, overrides SEO)
            schedule_timezone: "IST" or "EST"
            playlist_id: Optional playlist to add all videos to
            dry_run: If True, generate metadata but don't upload
        
        Returns:
            List of VideoMetadata with upload results
        """
        results = []
        schedule = self.scheduler.get_batch_schedule(
            count=len(videos),
            timezone=schedule_timezone,
        )

        quota = self.uploader.get_quota_status()
        self.logger.info(
            f"Batch upload: {len(videos)} videos | "
            f"Quota remaining: {quota['remaining']} ({quota['max_uploads_remaining']} uploads)"
        )

        for i, video_info in enumerate(videos):
            self.logger.info(f"\n{'='*50}")
            self.logger.info(f"Video {i+1}/{len(videos)}: {video_info.get('topic', 'Unknown')}")

            try:
                # Generate or use provided metadata
                if video_info.get("title") and video_info.get("description"):
                    metadata = VideoMetadata(
                        title=video_info["title"],
                        description=video_info["description"],
                        tags=video_info.get("tags", []),
                    )
                else:
                    metadata = self.seo.generate(
                        topic=video_info.get("topic", ""),
                        script_summary=video_info.get("script_summary", ""),
                    )

                # Apply scheduling and playlist
                metadata.publish_at = schedule[i]
                metadata.privacy_status = "private"  # will auto-publish at scheduled time
                metadata.thumbnail_path = video_info.get("thumbnail_path")
                metadata.playlist_id = playlist_id

                if dry_run:
                    self.logger.info(f"  [DRY RUN] Would upload: {metadata.title}")
                    self.logger.info(f"  Scheduled: {metadata.publish_at}")
                    results.append(metadata)
                    continue

                # Upload
                result = self.uploader.upload(
                    video_path=video_info["video_path"],
                    metadata=metadata,
                )
                results.append(result)

                # Respect quota — pause between uploads
                if i < len(videos) - 1:
                    self.logger.info("  Waiting 30s between uploads (quota safety)...")
                    time.sleep(30)

            except QuotaExceededError as e:
                self.logger.error(f"  Quota exceeded: {e}")
                self.logger.info(f"  Stopping batch. {i} of {len(videos)} uploaded.")
                break
            except Exception as e:
                self.logger.error(f"  Failed: {e}")
                results.append(VideoMetadata(title=video_info.get("topic", "FAILED")))

        self.logger.info(f"\nBatch complete: {len([r for r in results if r.video_id])}/{len(videos)} uploaded")
        return results


# ─── Custom Exceptions ────────────────────────────────────────────────────────

class UploadError(Exception):
    """Raised when a video upload fails."""
    pass

class QuotaExceededError(Exception):
    """Raised when daily YouTube API quota would be exceeded."""
    pass


# ─── CLI ENTRY POINT ─────────────────────────────────────────────────────────

def main():
    """Command-line interface for the uploader."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="YouTube Uploader — Faceless Automation Pipeline")
    parser.add_argument("--setup", action="store_true", help="Run first-time OAuth setup")
    parser.add_argument("--upload", type=str, help="Path to video file to upload")
    parser.add_argument("--title", type=str, help="Video title")
    parser.add_argument("--description", type=str, default="", help="Video description")
    parser.add_argument("--tags", type=str, default="", help="Comma-separated tags")
    parser.add_argument("--privacy", type=str, default="private", choices=["public", "private", "unlisted"])
    parser.add_argument("--thumbnail", type=str, help="Path to thumbnail image")
    parser.add_argument("--schedule", type=str, help="Publish time (ISO 8601)")
    parser.add_argument("--playlist", type=str, help="Playlist ID to add video to")
    parser.add_argument("--quota", action="store_true", help="Show quota status")
    parser.add_argument("--history", action="store_true", help="Show upload history")
    parser.add_argument("--stats", type=str, help="Get stats for a video ID")
    parser.add_argument("--channel", action="store_true", help="Show channel stats")
    parser.add_argument("--publish", type=str, help="Make a private video public (video ID)")
    args = parser.parse_args()

    uploader = YouTubeUploader()

    if args.setup:
        print("Running first-time OAuth setup...")
        print("A browser window will open. Log in and authorise the application.")
        uploader.authenticate()
        print("Setup complete! Token saved. You can now upload videos.")
        return

    if args.quota:
        uploader.authenticate()
        status = uploader.get_quota_status()
        print(f"\nQuota Status ({status['date']}):")
        print(f"  Used: {status['used']} / {status['limit']}")
        print(f"  Remaining: {status['remaining']}")
        print(f"  Uploads possible: {status['max_uploads_remaining']}")
        return

    if args.history:
        history = uploader.get_upload_history()
        print(f"\nRecent Uploads ({len(history)}):")
        for h in history:
            print(f"  [{h['upload_date']}] {h['title']}")
            print(f"    → https://youtube.com/watch?v={h['video_id']}")
        return

    if args.stats:
        uploader.authenticate()
        stats = uploader.get_video_stats(args.stats)
        print(f"\nVideo Stats: {stats.get('title')}")
        print(f"  Views: {stats.get('view_count', 0):,}")
        print(f"  Likes: {stats.get('like_count', 0):,}")
        print(f"  Comments: {stats.get('comment_count', 0):,}")
        return

    if args.channel:
        uploader.authenticate()
        stats = uploader.get_channel_stats()
        print(f"\nChannel: {stats.get('channel_name')}")
        print(f"  Subscribers: {stats.get('subscriber_count', 0):,}")
        print(f"  Videos: {stats.get('video_count', 0):,}")
        print(f"  Total Views: {stats.get('view_count', 0):,}")
        return

    if args.publish:
        uploader.authenticate()
        uploader.publish_video(args.publish)
        print(f"Video {args.publish} is now PUBLIC")
        return

    if args.upload:
        if not args.title:
            print("Error: --title is required for upload")
            return

        uploader.authenticate()

        metadata = VideoMetadata(
            title=args.title,
            description=args.description,
            tags=[t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else [],
            privacy_status=args.privacy,
            thumbnail_path=args.thumbnail,
            publish_at=args.schedule,
            playlist_id=args.playlist,
        )

        result = uploader.upload(args.upload, metadata)
        print(f"\nUpload successful!")
        print(f"  Video ID: {result.video_id}")
        print(f"  URL: {result.video_url}")
        if result.publish_at:
            print(f"  Scheduled: {result.publish_at}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
