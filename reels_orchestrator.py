"""
REELS ORCHESTRATOR — Short-Form Vertical Video Pipeline
=========================================================
End-to-end pipeline for YouTube Shorts + Instagram Reels + Facebook Reels.

Flow: Trending Topic → Punchy Script (30-60s) → TTS → Images → 
      Vertical Video (1080x1920) → Big Subtitles → Upload to 3 platforms

Designed for 2-3 Reels per day across multiple niches.

Usage:
    # Generate 3 reels
    python reels_orchestrator.py --count 3

    # Specific niche
    python reels_orchestrator.py --niche ai_tools --count 2

    # Multi-niche (1 reel per niche)
    python reels_orchestrator.py --multi-niche ai_tools,tech,finance

    # Dry run
    python reels_orchestrator.py --dry-run --count 3

Cron (3 reels daily at 8 AM IST):
    0 8 * * * cd C:\\youtube-automation && venv\\Scripts\\python reels_orchestrator.py --count 3

Dependencies: Same as main pipeline + instagrapi (for Instagram)
    pip install instagrapi facebook-sdk
"""

import os
import sys
import json
import time
import random
import logging
import datetime
import argparse
import subprocess
import traceback
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from topic_and_script import TopicResearcher, ScriptWriter, Topic, Script, ScriptScene, NICHE_CONFIG
from video_assembler import VideoConfig, VideoAssembler, Scene, assemble_faceless_video
from youtube_uploader import YouTubeUploader, VideoMetadata, UploaderConfig, PublishScheduler


# ═══════════════════════════════════════════════════════════════
#  REELS CONFIGURATION
# ═══════════════════════════════════════════════════════════════

@dataclass
class ReelsConfig:
    """Configuration for short-form vertical video production."""

    # Video specs (9:16 vertical)
    width: int = 1080
    height: int = 1920
    fps: int = 30
    max_duration: int = 59           # YouTube Shorts max = 60s
    target_duration: int = 45        # Sweet spot for engagement
    target_words: int = 120          # ~45 sec at fast narration pace

    # Visual style
    subtitle_font_size: int = 72     # Bigger for mobile screens
    subtitle_margin_bottom: int = 350 # Above the engagement buttons area
    default_effect: str = "zoom_in"  # Faster effects for short content
    crossfade: float = 0.0           # No crossfade — hard cuts feel punchier
    bg_music_volume: float = 0.12    # Slightly louder than long-form

    # Encoding
    codec: str = "libx264"
    preset: str = "medium"
    crf: int = 18                    # Higher quality for short content
    
    # Content
    scenes_per_reel: int = 4         # 3-5 scenes for a 45-60s reel
    niche: str = "ai_tools"
    tone: str = "casual_fun"         # Short-form works better casual
    llm_provider: str = os.getenv("LLM_PROVIDER", "anthropic")
    tts_provider: str = os.getenv("TTS_PROVIDER", "elevenlabs")
    image_provider: str = os.getenv("IMAGE_PROVIDER", "pexels")

    # TTS
    tts_voice_id: str = os.getenv("TTS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")
    tts_model: str = os.getenv("TTS_MODEL", "eleven_multilingual_v2")

    # Paths
    output_dir: str = os.path.join(os.getcwd(), "output", "reels")
    temp_dir: str = os.path.join(os.getcwd(), "tmp", "reels")
    music_dir: str = os.path.join(os.getcwd(), "assets", "music")

    # Upload platforms
    upload_youtube: bool = os.getenv("REELS_YOUTUBE", "true").lower() == "true"
    upload_instagram: bool = os.getenv("REELS_INSTAGRAM", "false").lower() == "true"
    upload_facebook: bool = os.getenv("REELS_FACEBOOK", "false").lower() == "true"

    # Instagram credentials
    ig_username: str = os.getenv("INSTAGRAM_USERNAME", "")
    ig_password: str = os.getenv("INSTAGRAM_PASSWORD", "")

    # Facebook credentials
    fb_page_id: str = os.getenv("FACEBOOK_PAGE_ID", "")
    fb_access_token: str = os.getenv("FACEBOOK_ACCESS_TOKEN", "")

    # Notifications
    slack_webhook: Optional[str] = os.getenv("SLACK_WEBHOOK_URL")
    telegram_token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")


# ═══════════════════════════════════════════════════════════════
#  REELS SCRIPT WRITER (specialised for short-form)
# ═══════════════════════════════════════════════════════════════

class ReelsScriptWriter:
    """
    Generates ultra-short, punchy scripts optimised for Reels/Shorts.
    
    Key differences from long-form:
        - 100-130 words max (45-55 seconds)
        - Hook in first 2 seconds (not 10)
        - No CTA / subscribe prompt (wastes precious seconds)
        - Fast scene changes every 8-12 seconds
        - Cliffhanger or surprising end to trigger replays
    """

    # Multi-niche topic templates for trending content
    REEL_TEMPLATES = {
        "ai_tools": [
            "This new AI tool just replaced a $500/month subscription",
            "{tool_name} can now do something impossible — here's what changed",
            "3 AI tools that went viral this week and why you should care",
            "I tested the newest AI tool so you don't have to — here's the truth",
            "This AI trick saves 5 hours every week — most people don't know it exists",
            "AI just made this entire profession 10x faster overnight",
        ],
        "tech": [
            "This tech update changes everything — here's what you missed",
            "The internet is going crazy over this new feature",
            "3 tech trends that will blow up in the next 6 months",
            "This hidden phone feature is mind-blowing — try it now",
        ],
        "finance": [
            "This money mistake is costing you lakhs every year",
            "3 investment rules that actually work in 2026",
            "How to save your first 1 lakh — the simple math nobody teaches you",
            "This financial hack is going viral for a reason",
        ],
        "trending": [
            "The internet can't stop talking about this — here's why",
            "This just broke the internet — here's the full story in 60 seconds",
            "Everyone is wrong about this trending topic — here's the truth",
            "3 things that went viral today and what they actually mean",
        ],
    }

    def __init__(self, provider: str = "anthropic"):
        self.provider = provider
        self.writer = ScriptWriter(provider=provider)
        self.logger = logging.getLogger("ReelsScriptWriter")

    def generate(
        self, topic: str | Topic, niche: str = "ai_tools", config: ReelsConfig = None
    ) -> Script:
        """Generate a short-form script optimised for Reels/Shorts."""
        cfg = config or ReelsConfig()
        topic_str = topic.title if isinstance(topic, Topic) else topic

        prompt = self._build_reels_prompt(topic_str, niche, cfg)
        raw = self.writer._call_llm(prompt)
        script = self.writer._parse_response(raw, topic_str)

        # Enforce duration limits
        total_words = sum(len(s.narration.split()) for s in script.scenes)
        if total_words > cfg.target_words + 30:
            self.logger.warning(
                f"  Script too long ({total_words} words), truncating to {cfg.scenes_per_reel} scenes"
            )
            script.scenes = script.scenes[:cfg.scenes_per_reel]

        # Recalculate durations (faster pace for reels: ~2.8 words/sec)
        for scene in script.scenes:
            words = len(scene.narration.split())
            scene.duration_estimate = words / 2.8

        script.total_words = sum(len(s.narration.split()) for s in script.scenes)
        script.total_duration = sum(s.duration_estimate for s in script.scenes)

        self.logger.info(
            f"  Reel script: {len(script.scenes)} scenes, "
            f"{script.total_words} words, ~{script.total_duration:.0f}s"
        )
        return script

    def _build_reels_prompt(self, topic: str, niche: str, cfg: ReelsConfig) -> str:
        return f"""You are a viral short-form video scriptwriter. Write a YouTube Shorts / Instagram Reels script.

TOPIC: {topic}
NICHE: {niche}
TARGET: Exactly {cfg.target_words} words ({cfg.target_duration} seconds)
SCENES: Exactly {cfg.scenes_per_reel} scenes

CRITICAL RULES FOR SHORT-FORM:
1. HOOK IN FIRST 2 SECONDS — one explosive sentence that stops the scroll.
   Examples: "This changes everything." / "Nobody is talking about this." / "Delete this app right now."
2. NO FILLER — every single word must earn its place. No "hey guys", no "in this video", no "welcome".
3. FAST PACING — each scene is 8-15 seconds max. Hard cuts between scenes.
4. SURPRISE ENDING — end with a twist, shocking fact, or cliffhanger that makes people replay.
5. NO CTA — don't say "subscribe" or "follow". Let the content speak. Algorithm rewards replays, not asks.
6. VISUAL VARIETY — each scene must have a completely different visual (no two similar images).
7. VERTICAL FORMAT — all visual cues must work in 9:16 portrait orientation.

OUTPUT: Return ONLY valid JSON (no markdown):
{{
  "title": "Short punchy title under 50 chars (with emoji)",
  "hook_text": "The exact first sentence (2 seconds)",
  "scenes": [
    {{
      "scene_id": 0,
      "scene_type": "hook",
      "narration": "One explosive opening sentence that stops scrolling...",
      "visual_cue": "VERTICAL: Close-up of [specific visual], dramatic lighting, phone-screen composition",
      "on_screen_text": "KEY PHRASE IN CAPS (3-5 words max)",
      "transition": "cut"
    }},
    {{
      "scene_id": 1,
      "scene_type": "content",
      "narration": "Rapid-fire point that builds on the hook...",
      "visual_cue": "VERTICAL: [specific visual different from scene 0]",
      "on_screen_text": "BOLD TEXT OVERLAY",
      "transition": "cut"
    }}
  ],
  "tags": ["tag1", "tag2", ...],
  "description_seo": "One-line description with keywords for all platforms",
  "thumbnail_concept": "Eye-catching vertical thumbnail idea"
}}

Write EXACTLY {cfg.scenes_per_reel} scenes totaling {cfg.target_words} words. Every word counts."""

    def get_trending_reel_topics(self, niche: str, count: int = 3) -> List[str]:
        """Get quick trending topics specifically for reels."""
        templates = self.REEL_TEMPLATES.get(niche, self.REEL_TEMPLATES["trending"])
        trending_topics = []

        # Mix templates with real trending data
        researcher = TopicResearcher(niche=niche)
        try:
            real_topics = researcher.discover(count=count, sources=["google_trends", "reddit"])
            for t in real_topics:
                trending_topics.append(t.title)
        except Exception:
            pass

        # Fill remaining with templates
        while len(trending_topics) < count:
            template = random.choice(templates)
            trending_topics.append(template)

        return trending_topics[:count]


# ═══════════════════════════════════════════════════════════════
#  REELS VIDEO ASSEMBLER (vertical format tweaks)
# ═══════════════════════════════════════════════════════════════

class ReelsVideoAssembler:
    """
    Assembles vertical 9:16 videos with Reels-optimised settings.
    
    Differences from long-form assembler:
        - 1080x1920 resolution (vertical)
        - Larger subtitles (72px) positioned above engagement buttons
        - Faster effects (zoom rather than slow Ken Burns)
        - No crossfades (hard cuts are punchier)
        - Higher quality encoding (CRF 18)
    """

    def __init__(self, config: ReelsConfig):
        self.cfg = config
        self.logger = logging.getLogger("ReelsAssembler")

    def assemble(
        self,
        scenes_data: List[Dict],
        output_path: str,
        bg_music_path: Optional[str] = None,
    ) -> str:
        """Assemble a vertical Reel/Short from scenes."""

        video_config = VideoConfig(
            width=self.cfg.width,
            height=self.cfg.height,
            fps=self.cfg.fps,
            preset=self.cfg.preset,
            crf=self.cfg.crf,
            default_image_effect=self.cfg.default_effect,
            crossfade_duration=self.cfg.crossfade,
            subtitle_font_size=self.cfg.subtitle_font_size,
            subtitle_margin_bottom=self.cfg.subtitle_margin_bottom,
            bg_music_volume=self.cfg.bg_music_volume,
            temp_dir=self.cfg.temp_dir,
            output_dir=self.cfg.output_dir,
        )

        result = assemble_faceless_video(
            scenes_data=scenes_data,
            output_filename=output_path,
            bg_music_path=bg_music_path,
            config=video_config,
            use_whisper_subs=False,
            shorts_mode=False,  # We're already passing vertical dimensions
        )

        # Verify duration is under 60 seconds
        duration = self._get_duration(result)
        if duration > 60:
            self.logger.warning(f"  Reel is {duration:.0f}s (over 60s limit), trimming...")
            trimmed_path = result.replace(".mp4", "_trimmed.mp4")
            self._trim_to_60s(result, trimmed_path)
            os.replace(trimmed_path, result)

        self.logger.info(f"  Reel assembled: {result} ({duration:.0f}s)")
        return result

    def _get_duration(self, path: str) -> float:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True
        )
        try:
            return float(result.stdout.strip())
        except ValueError:
            return 0

    def _trim_to_60s(self, input_path: str, output_path: str):
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-t", "59", "-c:v", "copy", "-c:a", "copy",
            output_path
        ], capture_output=True)


# ═══════════════════════════════════════════════════════════════
#  MULTI-PLATFORM UPLOADER
# ═══════════════════════════════════════════════════════════════

class MultiPlatformUploader:
    """
    Upload Reels to YouTube Shorts, Instagram Reels, and Facebook Reels.
    """

    def __init__(self, config: ReelsConfig):
        self.cfg = config
        self.logger = logging.getLogger("MultiUploader")

    def upload_all(
        self, video_path: str, title: str, description: str,
        tags: List[str], thumbnail_path: Optional[str] = None,
    ) -> Dict:
        """Upload to all enabled platforms. Returns results dict."""
        results = {}

        # YouTube Shorts
        if self.cfg.upload_youtube:
            try:
                yt_result = self._upload_youtube(video_path, title, description, tags, thumbnail_path)
                results["youtube"] = yt_result
                self.logger.info(f"  YouTube Shorts: {yt_result.get('url', 'uploaded')}")
            except Exception as e:
                results["youtube"] = {"error": str(e)}
                self.logger.error(f"  YouTube upload failed: {e}")

        # Instagram Reels
        if self.cfg.upload_instagram and self.cfg.ig_username:
            try:
                ig_result = self._upload_instagram(video_path, title, description, tags)
                results["instagram"] = ig_result
                self.logger.info(f"  Instagram Reels: uploaded")
            except Exception as e:
                results["instagram"] = {"error": str(e)}
                self.logger.error(f"  Instagram upload failed: {e}")

        # Facebook Reels
        if self.cfg.upload_facebook and self.cfg.fb_access_token:
            try:
                fb_result = self._upload_facebook(video_path, title, description)
                results["facebook"] = fb_result
                self.logger.info(f"  Facebook Reels: uploaded")
            except Exception as e:
                results["facebook"] = {"error": str(e)}
                self.logger.error(f"  Facebook upload failed: {e}")

        return results

    def _upload_youtube(
        self, video_path, title, description, tags, thumbnail_path
    ) -> Dict:
        """Upload as YouTube Shorts (must be ≤60s and vertical)."""
        uploader_config = UploaderConfig(
            slack_webhook_url=self.cfg.slack_webhook,
            telegram_bot_token=self.cfg.telegram_token,
            telegram_chat_id=self.cfg.telegram_chat,
        )
        uploader = YouTubeUploader(uploader_config)
        uploader.authenticate()

        # Add #Shorts to title/description for YouTube to recognise it
        if "#Shorts" not in title:
            title = f"{title} #Shorts"

        metadata = VideoMetadata(
            title=title[:100],
            description=f"{description}\n\n#Shorts #AI #FutureProofAI",
            tags=tags + ["Shorts"],
            category_id="28",
            privacy_status="public",  # Shorts should go public immediately
            thumbnail_path=thumbnail_path,
            made_for_kids=False,
        )

        result = uploader.upload(video_path=video_path, metadata=metadata)
        return {
            "platform": "youtube",
            "video_id": result.video_id,
            "url": result.video_url,
        }

    def _upload_instagram(self, video_path, title, description, tags) -> Dict:
        """Upload as Instagram Reels using instagrapi."""
        try:
            from instagrapi import Client
        except ImportError:
            raise ImportError(
                "Install instagrapi: pip install instagrapi"
            )

        cl = Client()

        # Login (handles 2FA, challenge, etc.)
        session_path = os.path.join("config", "ig_session.json")
        if os.path.exists(session_path):
            cl.load_settings(session_path)
            cl.login(self.cfg.ig_username, self.cfg.ig_password)
        else:
            cl.login(self.cfg.ig_username, self.cfg.ig_password)
            cl.dump_settings(session_path)

        # Build caption with hashtags
        hashtags = " ".join(f"#{t.replace(' ', '')}" for t in tags[:20])
        caption = f"{title}\n\n{description}\n\n{hashtags}"

        # Upload reel
        media = cl.clip_upload(
            path=video_path,
            caption=caption[:2200],  # Instagram caption limit
        )

        return {
            "platform": "instagram",
            "media_id": str(media.pk),
            "url": f"https://www.instagram.com/reel/{media.code}/",
        }

    def _upload_facebook(self, video_path, title, description) -> Dict:
        """Upload as Facebook Reels using Graph API."""
        import requests

        page_id = self.cfg.fb_page_id
        access_token = self.cfg.fb_access_token

        # Step 1: Initialize upload
        init_resp = requests.post(
            f"https://graph.facebook.com/v18.0/{page_id}/video_reels",
            params={"access_token": access_token},
            json={"upload_phase": "start"},
        )
        init_resp.raise_for_status()
        video_id = init_resp.json()["video_id"]

        # Step 2: Upload video file
        file_size = os.path.getsize(video_path)
        with open(video_path, "rb") as f:
            upload_resp = requests.post(
                f"https://rupload.facebook.com/video-upload/v18.0/{video_id}",
                headers={
                    "Authorization": f"OAuth {access_token}",
                    "offset": "0",
                    "file_size": str(file_size),
                    "Content-Type": "application/octet-stream",
                },
                data=f,
            )
            upload_resp.raise_for_status()

        # Step 3: Publish
        publish_resp = requests.post(
            f"https://graph.facebook.com/v18.0/{page_id}/video_reels",
            params={"access_token": access_token},
            json={
                "upload_phase": "finish",
                "video_id": video_id,
                "title": title[:100],
                "description": description[:500],
            },
        )
        publish_resp.raise_for_status()

        return {
            "platform": "facebook",
            "video_id": video_id,
            "url": f"https://www.facebook.com/reel/{video_id}",
        }


# ═══════════════════════════════════════════════════════════════
#  TTS ENGINE (reused from orchestrator)
# ═══════════════════════════════════════════════════════════════

class ReelsTTSEngine:
    """Lightweight TTS wrapper for Reels."""

    def __init__(self, provider: str = "elevenlabs", voice_id: str = "", model: str = ""):
        self.provider = provider
        self.voice_id = voice_id
        self.model = model
        self.logger = logging.getLogger("ReelsTTS")

    def generate(self, scenes: List[ScriptScene], output_dir: str) -> List[str]:
        os.makedirs(output_dir, exist_ok=True)
        audio_paths = []

        for scene in scenes:
            path = os.path.join(output_dir, f"audio_{scene.scene_id:03d}.mp3")

            if not scene.narration.strip():
                audio_paths.append(path)
                continue

            try:
                if self.provider == "elevenlabs":
                    from elevenlabs import ElevenLabs, VoiceSettings
                    client = ElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY"))
                    audio = client.text_to_speech.convert(
                        voice_id=self.voice_id or "pNInz6obpgDQGcFmaJgB",
                        model_id=self.model or "eleven_multilingual_v2",
                        text=scene.narration,
                        voice_settings=VoiceSettings(stability=0.25, similarity_boost=0.85),
                    )
                    with open(path, "wb") as f:
                        for chunk in audio:
                            f.write(chunk)
                elif self.provider == "openai":
                    import openai
                    client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                    response = client.audio.speech.create(
                        model="tts-1-hd", voice="onyx", input=scene.narration,
                    )
                    response.stream_to_file(path)

                self.logger.info(f"  TTS scene {scene.scene_id}: {len(scene.narration)} chars")
            except Exception as e:
                self.logger.error(f"  TTS failed scene {scene.scene_id}: {e}")
                # Create silent placeholder
                subprocess.run([
                    "ffmpeg", "-y", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=stereo", "-t", "5",
                    "-c:a", "libmp3lame", "-q:a", "9", path,
                ], capture_output=True)

            audio_paths.append(path)

        return audio_paths


# ═══════════════════════════════════════════════════════════════
#  IMAGE ENGINE (reused + vertical crop)
# ═══════════════════════════════════════════════════════════════

class ReelsImageEngine:
    """Image fetcher that returns portrait-friendly images."""

    def __init__(self, provider: str = "pexels"):
        self.provider = provider
        self.logger = logging.getLogger("ReelsImages")

    def generate(self, scenes: List[ScriptScene], output_dir: str) -> List[str]:
        os.makedirs(output_dir, exist_ok=True)
        image_paths = []

        for scene in scenes:
            path = os.path.join(output_dir, f"scene_{scene.scene_id:03d}.png")

            try:
                if self.provider in ("pexels", "both"):
                    self._fetch_pexels(scene.visual_cue, path)
                elif self.provider == "dalle":
                    self._fetch_dalle(scene.visual_cue, path)
                self.logger.info(f"  Image scene {scene.scene_id}")
            except Exception as e:
                self.logger.error(f"  Image failed scene {scene.scene_id}: {e}")
                self._create_placeholder(path, scene.on_screen_text or "")

            image_paths.append(path)

        return image_paths

    def _fetch_pexels(self, query: str, output_path: str):
        import requests
        api_key = os.environ.get("PEXELS_API_KEY")
        search_query = " ".join(query.split()[:6])

        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": search_query, "per_page": 5, "orientation": "portrait"},
            timeout=10,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos", [])

        if not photos:
            # Fallback broader search
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": api_key},
                params={"query": search_query.split()[0], "per_page": 5, "orientation": "portrait"},
                timeout=10,
            )
            resp.raise_for_status()
            photos = resp.json().get("photos", [])

        if photos:
            photo = random.choice(photos)
            img_url = photo["src"].get("large2x") or photo["src"].get("large")
            img_data = requests.get(img_url, timeout=30).content
            with open(output_path, "wb") as f:
                f.write(img_data)
        else:
            raise ValueError(f"No Pexels portrait results for: {search_query}")

    def _fetch_dalle(self, prompt: str, output_path: str):
        import openai, requests
        client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.images.generate(
            model="dall-e-3",
            prompt=f"VERTICAL portrait orientation, mobile-screen composition: {prompt}",
            size="1024x1792",
            quality="standard",
            n=1,
        )
        img_data = requests.get(response.data[0].url, timeout=30).content
        with open(output_path, "wb") as f:
            f.write(img_data)

    def _create_placeholder(self, path: str, text: str):
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (1080, 1920), color=(15, 15, 35))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 56)
        except (IOError, OSError):
            try:
                font = ImageFont.truetype("C:\\Windows\\Fonts\\arialbd.ttf", 56)
            except (IOError, OSError):
                font = ImageFont.load_default()
        if text:
            bbox = draw.textbbox((0, 0), text[:30], font=font)
            tw = bbox[2] - bbox[0]
            draw.text(((1080 - tw) // 2, 900), text[:30], fill="white", font=font)
        img.save(path)


# ═══════════════════════════════════════════════════════════════
#  MAIN REELS ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

class ReelsOrchestrator:
    """
    Master orchestrator for short-form vertical video production.

    Pipeline per reel:
        1. Discover trending topic
        2. Generate 45-second punchy script
        3. Generate TTS audio
        4. Fetch portrait images
        5. Assemble vertical video (1080x1920)
        6. Upload to YouTube Shorts + Instagram Reels + Facebook Reels
        7. Archive + notify
    """

    def __init__(self, config: ReelsConfig = None):
        self.cfg = config or ReelsConfig()
        self.logger = logging.getLogger("ReelsOrchestrator")
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        os.makedirs(self.cfg.temp_dir, exist_ok=True)

    def run(self, count: int = 3, niches: Optional[List[str]] = None, dry_run: bool = False) -> List[Dict]:
        """Produce multiple Reels."""
        run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"  REELS PIPELINE: {run_id} | {count} reels")
        self.logger.info(f"  Platforms: YT={self.cfg.upload_youtube} IG={self.cfg.upload_instagram} FB={self.cfg.upload_facebook}")
        self.logger.info(f"{'='*60}\n")

        # Determine niches for each reel
        if niches:
            niche_cycle = niches
        else:
            niche_cycle = [self.cfg.niche]

        results = []
        start = time.time()

        for i in range(count):
            niche = niche_cycle[i % len(niche_cycle)]
            self.logger.info(f"\n{'─'*40}")
            self.logger.info(f"  REEL {i+1}/{count} [{niche}]")
            self.logger.info(f"{'─'*40}")

            try:
                result = self._produce_single_reel(run_id, i, niche, dry_run)
                results.append(result)
            except Exception as e:
                self.logger.error(f"  Reel {i+1} failed: {e}")
                self.logger.debug(traceback.format_exc())
                results.append({"reel_num": i+1, "status": "FAILED", "error": str(e)})

            # Pause between reels to avoid API rate limits
            if i < count - 1 and not dry_run:
                self.logger.info("  Waiting 10s before next reel...")
                time.sleep(10)

        elapsed = time.time() - start
        success = sum(1 for r in results if r.get("status") == "SUCCESS")

        summary = (
            f"\n{'='*60}\n"
            f"  REELS COMPLETE: {success}/{count} reels in {elapsed/60:.1f} min\n"
            f"{'='*60}"
        )
        self.logger.info(summary)
        self._notify(summary)

        return results

    def _produce_single_reel(self, run_id: str, index: int, niche: str, dry_run: bool) -> Dict:
        """Produce a single Reel through all stages."""
        reel_id = f"{run_id}_reel{index:02d}"
        work_dir = os.path.join(self.cfg.temp_dir, reel_id)
        audio_dir = os.path.join(work_dir, "audio")
        image_dir = os.path.join(work_dir, "images")
        os.makedirs(audio_dir, exist_ok=True)
        os.makedirs(image_dir, exist_ok=True)

        result = {"reel_num": index + 1, "niche": niche, "status": "STARTED"}

        # ── 1. Topic ──
        self.logger.info("[1/6] Finding trending topic...")
        researcher = TopicResearcher(niche=niche, db_path=os.path.join("data", "topics.db"))
        topics = researcher.discover(count=1)
        if not topics:
            # Fallback to template
            script_writer = ReelsScriptWriter(self.cfg.llm_provider)
            fallback_topics = script_writer.get_trending_reel_topics(niche, 1)
            topic_str = fallback_topics[0] if fallback_topics else f"Trending {niche} update"
            topic = Topic(title=topic_str, source="template")
        else:
            topic = topics[0]

        result["topic"] = topic.title
        self.logger.info(f"  Topic: {topic.title}")

        # ── 2. Script ──
        self.logger.info("[2/6] Generating reel script...")
        writer = ReelsScriptWriter(self.cfg.llm_provider)
        script = writer.generate(topic=topic, niche=niche, config=self.cfg)
        researcher.mark_used(topic)

        result["title"] = script.title
        result["words"] = script.total_words
        result["duration_est"] = f"{script.total_duration:.0f}s"
        self.logger.info(f"  Script: {script.title} ({script.total_words} words, ~{script.total_duration:.0f}s)")

        if dry_run:
            result["status"] = "DRY_RUN"
            self.logger.info("  [DRY RUN] Skipping production.")
            return result

        # ── 3. TTS ──
        self.logger.info("[3/6] Generating TTS audio...")
        tts = ReelsTTSEngine(self.cfg.tts_provider, self.cfg.tts_voice_id, self.cfg.tts_model)
        audio_paths = tts.generate(script.scenes, audio_dir)

        # ── 4. Images ──
        self.logger.info("[4/6] Fetching portrait images...")
        img_engine = ReelsImageEngine(self.cfg.image_provider)
        image_paths = img_engine.generate(script.scenes, image_dir)

        # ── 5. Assemble ──
        self.logger.info("[5/6] Assembling vertical video...")
        effects = ["zoom_in", "zoom_out", "kenburns", "static_breathe"]
        scenes_data = []
        for i, scene in enumerate(script.scenes):
            if i < len(audio_paths) and i < len(image_paths):
                scenes_data.append({
                    "image_path": image_paths[i],
                    "audio_path": audio_paths[i],
                    "text": scene.narration,
                    "effect": effects[i % len(effects)],
                })

        bg_music = self._find_bg_music()
        assembler = ReelsVideoAssembler(self.cfg)
        output_filename = f"{reel_id}.mp4"
        video_path = assembler.assemble(scenes_data, output_filename, bg_music)

        result["video_path"] = video_path
        size_mb = os.path.getsize(video_path) / (1024 * 1024)
        result["size_mb"] = round(size_mb, 1)

        # ── 6. Upload ──
        self.logger.info("[6/6] Uploading to platforms...")
        description = script.description_seo or script.title
        uploader = MultiPlatformUploader(self.cfg)
        upload_results = uploader.upload_all(
            video_path=video_path,
            title=script.title,
            description=description,
            tags=script.tags,
        )
        result["uploads"] = upload_results
        result["status"] = "SUCCESS"

        # Cleanup
        import shutil
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

        return result

    def _find_bg_music(self) -> Optional[str]:
        music_dir = self.cfg.music_dir
        if not os.path.exists(music_dir):
            return None
        tracks = [f for f in os.listdir(music_dir) if f.endswith((".mp3", ".wav", ".m4a"))]
        if tracks:
            return os.path.join(music_dir, random.choice(tracks))
        return None

    def _notify(self, message: str):
        if self.cfg.slack_webhook:
            try:
                import requests
                requests.post(self.cfg.slack_webhook, json={"text": message}, timeout=10)
            except Exception:
                pass
        if self.cfg.telegram_token and self.cfg.telegram_chat:
            try:
                import requests
                requests.post(
                    f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage",
                    json={"chat_id": self.cfg.telegram_chat, "text": message},
                    timeout=10,
                )
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Reels/Shorts Pipeline — FutureProof AI",
        epilog="""
Examples:
    python reels_orchestrator.py --count 3
    python reels_orchestrator.py --niche ai_tools --count 2
    python reels_orchestrator.py --multi-niche ai_tools,finance,tech
    python reels_orchestrator.py --dry-run --count 3
        """
    )
    parser.add_argument("--count", type=int, default=3, help="Number of reels to produce")
    parser.add_argument("--niche", type=str, default="ai_tools", help="Content niche")
    parser.add_argument("--multi-niche", type=str, help="Comma-separated niches (1 reel per niche)")
    parser.add_argument("--dry-run", action="store_true", help="Skip TTS/images/upload")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)-18s] %(levelname)-7s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(f"logs/reels_{datetime.date.today().isoformat()}.log", encoding="utf-8"),
        ],
    )

    config = ReelsConfig(niche=args.niche)
    orchestrator = ReelsOrchestrator(config)

    niches = None
    if args.multi_niche:
        niches = [n.strip() for n in args.multi_niche.split(",")]

    results = orchestrator.run(count=args.count, niches=niches, dry_run=args.dry_run)

    print(f"\n{'='*50}")
    print("REELS RESULTS")
    print(f"{'='*50}")
    for r in results:
        status = r.get("status", "?")
        emoji = {"SUCCESS": "OK", "DRY_RUN": "DRY", "FAILED": "FAIL"}.get(status, "?")
        print(f"\n  [{emoji}] Reel {r.get('reel_num', '?')} [{r.get('niche', '')}]")
        print(f"       {r.get('title', 'N/A')}")
        if r.get("uploads"):
            for platform, info in r["uploads"].items():
                url = info.get("url", info.get("error", "N/A"))
                print(f"       {platform}: {url}")


if __name__ == "__main__":
    main()
