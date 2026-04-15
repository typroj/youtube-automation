"""
UNIFIED ORCHESTRATOR v4 — Long-Form + Reels Pipeline (ALL FIXES)
=================================================================
  Long-form:  python orchestrator.py --niche ai_tools --count 1
  Reels:      python orchestrator.py --reels --niche ai_tools --count 3
  Both:       python orchestrator.py --niche ai_tools --count 1 && python orchestrator.py --reels --count 2

Uses slideshow_assembler v4 (subtitle fix, correct font sizes, music before subs).
"""

import os, sys, json, time, shutil, random, logging, datetime, argparse, traceback

# Fix Windows console Unicode crash (emojis, arrows, Hindi characters in log output)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from typing import List, Dict, Optional
from dataclasses import asdict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError: pass

from topic_and_script import TopicResearcher, ScriptWriter, Topic, Script, ScriptScene, NICHE_CONFIG
from slideshow_assembler import (SlideshowAssembler, SlideshowConfig, generate_image_prompts,
    fetch_pexels_images, fetch_pexels_videos, generate_ass_with_highlights, generate_ass_whisper_with_highlights)
from youtube_uploader import (UploaderConfig, YouTubeUploader, VideoMetadata,
    PublishScheduler, QuotaExceededError)
from analytics_tracker import (refresh_instagram_analytics, weighted_niche_pick,
    boost_topics_by_niche, get_niche_weights, print_performance_report)


# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

def _env(k, d=""): return os.getenv(k) or d
def _env_int(k, d):
    v = os.getenv(k)
    try: return int(v) if v and v.strip() else d
    except: return d
def _env_float(k, d):
    v = os.getenv(k)
    try: return float(v) if v and v.strip() else d
    except: return d
def _env_bool(k, d=False):
    v = os.getenv(k)
    return v.strip().lower() in ("true","1","yes") if v and v.strip() else d

class PipelineConfig:
    def __init__(self):
        self.niche = _env("NICHE", "ai_tools")
        self.videos_per_run = _env_int("VIDEOS_PER_RUN", 1)
        self.video_length_minutes = _env_int("VIDEO_LENGTH", 10)
        self.tone = _env("TONE", "engaging_serious")
        self.schedule_timezone = _env("SCHEDULE_TZ", "IST")
        self.schedule_days_ahead = _env_int("SCHEDULE_DAYS_AHEAD", 1)
        self.images_longform = _env_int("IMAGES_LONGFORM", 20)
        self.images_reel = _env_int("IMAGES_REEL", 10)
        self.llm_provider = _env("LLM_PROVIDER", "anthropic")
        self.tts_provider = _env("TTS_PROVIDER", "elevenlabs")
        self.tts_voice_id = _env("TTS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")
        self.tts_model = _env("TTS_MODEL", "eleven_multilingual_v2")
        self.image_provider = _env("IMAGE_PROVIDER", "pexels")
        self.video_width = _env_int("VIDEO_WIDTH", 1920)
        self.video_height = _env_int("VIDEO_HEIGHT", 1080)
        self.video_fps = _env_int("VIDEO_FPS", 30)
        self.video_crf = _env_int("VIDEO_CRF", 20)
        self.video_preset = _env("VIDEO_PRESET", "medium")
        self.bg_music_volume = _env_float("BG_MUSIC_VOLUME", 0.08)
        self.bg_music_dir = _env("BG_MUSIC_DIR", "assets/music")
        self.auto_upload = _env_bool("AUTO_UPLOAD")
        self.default_privacy = _env("DEFAULT_PRIVACY", "private")
        self.playlist_id = _env("PLAYLIST_ID") or None
        self.reels_youtube = _env_bool("REELS_YOUTUBE", True)
        self.reels_instagram = _env_bool("REELS_INSTAGRAM")
        self.reels_facebook = _env_bool("REELS_FACEBOOK")
        self.ig_username = _env("INSTAGRAM_USERNAME")
        self.ig_password = _env("INSTAGRAM_PASSWORD")
        self.fb_page_id = _env("FACEBOOK_PAGE_ID")
        self.fb_access_token = _env("FACEBOOK_ACCESS_TOKEN")
        self.slack_webhook = _env("SLACK_WEBHOOK_URL") or None
        self.telegram_token = _env("TELEGRAM_BOT_TOKEN") or None
        self.telegram_chat = _env("TELEGRAM_CHAT_ID") or None
        self.base_dir = os.getcwd()
        self.output_dir = os.path.join(self.base_dir, "output")
        self.temp_dir = os.path.join(self.base_dir, "tmp")
        self.logs_dir = os.path.join(self.base_dir, "logs")
        self.data_dir = os.path.join(self.base_dir, "data")
        self.scripts_dir = os.path.join(self.base_dir, "scripts_archive")
        self.assets_dir = os.path.join(self.base_dir, "assets")

    def apply_overrides(self, args):
        if getattr(args,"niche",None): self.niche = args.niche
        if getattr(args,"count",None): self.videos_per_run = args.count
        if getattr(args,"length",None): self.video_length_minutes = args.length
        if getattr(args,"tone",None): self.tone = args.tone
        if getattr(args,"provider",None): self.llm_provider = args.provider
        if getattr(args,"tts_provider",None): self.tts_provider = args.tts_provider
        if getattr(args,"upload",False): self.auto_upload = True
        if getattr(args,"privacy",None): self.default_privacy = args.privacy


# ═══════════════════════════════════════════════════════════════
#  TTS ENGINE
# ═══════════════════════════════════════════════════════════════

class TTSEngine:
    def __init__(self, provider="elevenlabs", voice_id="", model=""):
        self.provider, self.voice_id, self.model = provider, voice_id, model
        self.logger = logging.getLogger("TTSEngine")

    def generate_full_audio(self, full_text, output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        self.logger.info(f"  TTS [{self.provider}]: {len(full_text)} chars → {output_path}")
        if self.provider == "elevenlabs":
            from elevenlabs import ElevenLabs, VoiceSettings
            client = ElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY"))
            audio = client.text_to_speech.convert(
                voice_id=self.voice_id or "pNInz6obpgDQGcFmaJgB",
                model_id=self.model or "eleven_multilingual_v2", text=full_text,
                voice_settings=VoiceSettings(stability=0.3, similarity_boost=0.8))
            with open(output_path, "wb") as f:
                for chunk in audio: f.write(chunk)
        elif self.provider == "openai":
            import openai
            openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY")).audio.speech.create(
                model="tts-1-hd", voice="onyx", input=full_text).stream_to_file(output_path)
        elif self.provider == "minimax":
            self._minimax_tts(full_text, output_path)
        else:
            raise ValueError(f"Unknown TTS provider: {self.provider}")
        self.logger.info(f"  TTS complete")
        return output_path

    def _minimax_tts(self, text: str, output_path: str):
        """
        MiniMax T2A V2 TTS — no SDK needed, pure HTTP.

        Env vars:
          MINIMAX_API_KEY   — your MiniMax API key
          MINIMAX_VOICE_ID  — voice ID (default: English_Insightful_Speaker)
          MINIMAX_MODEL     — model name (default: speech-2.8-hd)
        """
        import requests, json as _json

        api_key  = os.environ.get("MINIMAX_API_KEY", "")
        if not api_key:
            raise ValueError("MINIMAX_API_KEY is not set in .env")

        voice_id = self.voice_id or os.environ.get("MINIMAX_VOICE_ID",
                                                    "English_Insightful_Speaker")
        model    = self.model    or os.environ.get("MINIMAX_MODEL", "speech-2.8-hd")

        # MiniMax has a 10 000-char limit per request — split if needed
        MAX_CHARS = 9500
        chunks    = [text[i:i+MAX_CHARS] for i in range(0, len(text), MAX_CHARS)]

        raw_audio = b""
        for idx, chunk in enumerate(chunks, 1):
            self.logger.info(f"  MiniMax chunk {idx}/{len(chunks)} ({len(chunk)} chars)…")
            resp = requests.post(
                "https://api.minimax.io/v1/t2a_v2",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "text": chunk,
                    "stream": False,
                    "language_boost": "auto",
                    "voice_setting": {
                        "voice_id": voice_id,
                        "speed": 1.0,
                        "vol": 1.0,
                        "pitch": 0,
                        "emotion": "calm",
                    },
                    "audio_setting": {
                        "format": "mp3",
                        "sample_rate": 44100,
                        "bitrate": 128000,
                        "channel": 1,
                    },
                },
                timeout=120,
            )

            if resp.status_code != 200:
                raise RuntimeError(
                    f"MiniMax TTS error {resp.status_code}: {resp.text[:300]}"
                )

            data = resp.json()
            base = data.get("base_resp", {})
            status_code = base.get("status_code", -1)
            status_msg  = base.get("status_msg", "unknown error")
            if status_code != 0:
                hints = {
                    1008: "Insufficient balance — top up your MiniMax account at platform.minimax.io",
                    1004: "Invalid API key — check MINIMAX_API_KEY in .env",
                    1013: "Invalid voice ID — check MINIMAX_VOICE_ID in .env (valid: English_Insightful_Speaker, English_Graceful_Lady, English_radiant_girl, English_Lucky_Robot)",
                }
                hint = hints.get(status_code, "")
                raise RuntimeError(
                    f"MiniMax API error (code {status_code}): {status_msg}"
                    + (f"\n  ► {hint}" if hint else "")
                )

            hex_audio = data["data"]["audio"]
            raw_audio += bytes.fromhex(hex_audio)

        with open(output_path, "wb") as f:
            f.write(raw_audio)
        self.logger.info(f"  MiniMax: {len(raw_audio)/1024:.1f} KB written")


# ═══════════════════════════════════════════════════════════════
#  MULTI-PLATFORM UPLOADER
# ═══════════════════════════════════════════════════════════════

class MultiPlatformUploader:
    def __init__(self, cfg): self.cfg, self.logger = cfg, logging.getLogger("MultiUploader")

    def upload_reel(self, video_path, title, description, tags, thumb_path=None):
        results = {}
        if self.cfg.reels_youtube:
            try: results["youtube"] = self._yt_short(video_path, title, description, tags, thumb_path)
            except Exception as e: results["youtube"] = {"error": str(e)}; self.logger.error(f"  YT: {e}")
        if self.cfg.reels_instagram and self.cfg.ig_username:
            try: results["instagram"] = self._ig_reel(video_path, title, description, tags)
            except Exception as e: results["instagram"] = {"error": str(e)}; self.logger.error(f"  IG: {e}")
        if self.cfg.reels_facebook and self.cfg.fb_access_token:
            try: results["facebook"] = self._fb_reel(video_path, title, description)
            except Exception as e: results["facebook"] = {"error": str(e)}; self.logger.error(f"  FB: {e}")
        return results

    def _yt_short(self, vp, title, desc, tags, thumb):
        up = YouTubeUploader(UploaderConfig(slack_webhook_url=self.cfg.slack_webhook,
            telegram_bot_token=self.cfg.telegram_token, telegram_chat_id=self.cfg.telegram_chat))
        up.authenticate()
        if "#Shorts" not in title: title = f"{title} #Shorts"
        r = up.upload(video_path=vp, metadata=VideoMetadata(title=title[:100],
            description=f"{desc}\n\n#Shorts #AI #FutureProofAI", tags=tags+["Shorts"],
            category_id="28", privacy_status="public", thumbnail_path=thumb, made_for_kids=False))
        self.logger.info(f"  YT Short: {r.video_url}")
        return {"platform":"youtube","url":r.video_url,"id":r.video_id}

    def _ig_reel(self, vp, title, desc, tags):
        from instagrapi import Client
        cl = Client()
        # Per-username session file — prevents stale tokens from a different account
        sp = os.path.join("config", f"ig_session_{self.cfg.ig_username}.json")
        if os.path.exists(sp):
            cl.load_settings(sp)
            cl.login(self.cfg.ig_username, self.cfg.ig_password)
        else:
            cl.login(self.cfg.ig_username, self.cfg.ig_password)
            cl.dump_settings(sp)
        ht = " ".join(f"#{t.replace(' ','')}" for t in tags[:20])
        media = cl.clip_upload(path=vp, caption=f"{title}\n\n{desc}\n\n{ht}"[:2200])
        url = f"https://www.instagram.com/reel/{media.code}/"
        self.logger.info(f"  IG: {url}")
        return {"platform":"instagram","url":url}

    def _fb_reel(self, vp, title, desc):
        import requests
        pid, tok = self.cfg.fb_page_id, self.cfg.fb_access_token
        init = requests.post(f"https://graph.facebook.com/v18.0/{pid}/video_reels",
            params={"access_token":tok}, json={"upload_phase":"start"}).json()
        vid = init["video_id"]
        with open(vp,"rb") as f:
            requests.post(f"https://rupload.facebook.com/video-upload/v18.0/{vid}",
                headers={"Authorization":f"OAuth {tok}","offset":"0",
                    "file_size":str(os.path.getsize(vp)),"Content-Type":"application/octet-stream"},data=f)
        requests.post(f"https://graph.facebook.com/v18.0/{pid}/video_reels",
            params={"access_token":tok},json={"upload_phase":"finish","video_id":vid,
                "title":title[:100],"description":desc[:500]})
        self.logger.info(f"  FB: https://facebook.com/reel/{vid}")
        return {"platform":"facebook","url":f"https://facebook.com/reel/{vid}"}


# ═══════════════════════════════════════════════════════════════
#  SCRIPT FILE PARSER  (--script-file support)
# ═══════════════════════════════════════════════════════════════

def parse_script_file(path: str) -> dict:
    """
    Parse a structured script file with sections:
      [TITLE], [HOOK], [BODY], [CLOSER], [HASHTAGS]
    Returns dict with keys: title, hook, body, closer, hashtags (list[str])
    """
    tag_map = {
        "[TITLE]": "title", "[HOOK]": "hook",
        "[BODY]": "body", "[CLOSER]": "closer", "[HASHTAGS]": "hashtags",
    }
    lines_by_section: dict = {k: [] for k in tag_map.values()}
    current = None

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            stripped = raw.strip()
            upper = stripped.upper()
            matched = next((v for k, v in tag_map.items() if upper == k), None)
            if matched:
                current = matched
                continue
            if current is not None:
                lines_by_section[current].append(stripped)

    def _join(key):
        return " ".join(l for l in lines_by_section[key] if l)

    raw_tags = _join("hashtags")
    tags = [t.lstrip("#") for t in raw_tags.split() if t]

    return {
        "title":    _join("title") or "Untitled",
        "hook":     _join("hook"),
        "body":     _join("body"),
        "closer":   _join("closer"),
        "hashtags": tags,
    }


def _build_script_from_parsed(parsed: dict) -> "Script":
    """Convert a parsed script-file dict into a Script dataclass."""
    title   = parsed["title"]
    hook    = parsed["hook"]
    body    = parsed["body"]
    closer  = parsed["closer"]
    tags    = parsed["hashtags"]
    WPS     = 2.5  # approximate words-per-second speaking rate

    def _dur(text): return max(1.0, len(text.split()) / WPS)

    scenes = []
    if hook:
        scenes.append(ScriptScene(
            scene_id=1, narration=hook,
            visual_cue=f"Attention-grabbing visual for: {hook[:60]}",
            duration_estimate=_dur(hook), scene_type="hook",
        ))
    if body:
        scenes.append(ScriptScene(
            scene_id=2, narration=body,
            visual_cue=f"Informative visual for: {body[:60]}",
            duration_estimate=_dur(body), scene_type="content",
        ))
    if closer:
        scenes.append(ScriptScene(
            scene_id=3, narration=closer,
            visual_cue=f"Closing call-to-action visual: {closer[:60]}",
            duration_estimate=_dur(closer), scene_type="cta",
        ))

    all_narration = " ".join(s.narration for s in scenes)
    return Script(
        topic=title, title=title, scenes=scenes,
        total_duration=sum(s.duration_estimate for s in scenes),
        total_words=len(all_narration.split()),
        hook_text=hook[:40] if hook else "",
        hook_question=hook[:40] if hook else "",
        cta_text=closer,
        description_seo=(hook + " " + body)[:300].strip(),
        tags=tags,
        thumbnail_concept=f"Bold text '{title}' with striking visual",
    )


# ═══════════════════════════════════════════════════════════════
#  ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

class PipelineOrchestrator:
    def __init__(self, cfg):
        self.cfg, self.logger = cfg, logging.getLogger("Orchestrator")
        for d in [cfg.output_dir, cfg.temp_dir, cfg.logs_dir, cfg.data_dir,
                  cfg.scripts_dir, cfg.assets_dir, os.path.join(cfg.assets_dir,"music")]:
            os.makedirs(d, exist_ok=True)

    def run(self, mode="longform", count=1, dry_run=False, niches=None,
            use_analytics=True, forced_topic=None, script_file=None):
        run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        niche_list = niches or [self.cfg.niche]
        db_path = os.path.join(self.cfg.data_dir, "topics.db")
        self.logger.info(f"\n{'='*70}\n  PIPELINE {run_id} | {mode} | {count} videos\n{'='*70}\n")
        results, start = [], time.time()
        for i in range(count):
            # If multiple niches available, pick weighted by Instagram performance
            if use_analytics and len(niche_list) > 1:
                niche = weighted_niche_pick(db_path, niche_list)
            else:
                niche = niche_list[i % len(niche_list)]
            is_reel = mode in ("reels","reel")
            self.logger.info(f"\n{'─'*50}\n  {'REEL' if is_reel else 'VIDEO'} {i+1}/{count} [{niche}]\n{'─'*50}")
            try:
                results.append(self._produce(run_id, i, niche, is_reel, dry_run,
                                             forced_topic=forced_topic, script_file=script_file))
            except QuotaExceededError as e:
                self.logger.error(f"  Quota: {e}"); break
            except Exception as e:
                self.logger.error(f"  Failed: {e}"); self.logger.debug(traceback.format_exc())
                results.append({"num":i+1,"status":"FAILED","error":str(e)})
            if i < count-1 and not dry_run: time.sleep(10)
        ok = sum(1 for r in results if r.get("status")=="SUCCESS")
        self.logger.info(f"\nDONE: {ok}/{count} in {(time.time()-start)/60:.1f} min")
        return results

    def _produce(self, run_id, idx, niche, is_reel, dry_run, forced_topic=None, script_file=None):
        vid_id = f"{run_id}_{'reel' if is_reel else 'vid'}{idx:02d}"
        work_dir = os.path.join(self.cfg.temp_dir, vid_id)
        os.makedirs(os.path.join(work_dir,"images"), exist_ok=True)
        result = {"num":idx+1,"type":"reel" if is_reel else "longform","status":"STARTED"}

        # 1. TOPIC
        self.logger.info("\n[1/9] Topic...")
        db_path = os.path.join(self.cfg.data_dir, "topics.db")
        researcher = None
        if script_file:
            # User provided a full script file — skip discovery AND script generation
            parsed = parse_script_file(script_file)
            topic = Topic(title=parsed["title"], source="script_file", score=100.0,
                          keyword=parsed["title"])
            self.logger.info(f"  [script_file] {topic.title}")
        elif forced_topic:
            # User supplied topic directly — skip discovery entirely
            topic = Topic(title=forced_topic, source="manual", score=100.0,
                          keyword=forced_topic)
            self.logger.info(f"  [manual] {topic.title}")
        else:
            researcher = TopicResearcher(niche=niche, db_path=db_path)
            topics = researcher.discover(count=5)
            if not topics: raise RuntimeError("No topics found")
            topics = boost_topics_by_niche(topics, niche, db_path)
            topics.sort(key=lambda t: t.score, reverse=True)
            topic = topics[0]
            self.logger.info(f"  [{topic.score:.0f}] {topic.title} (niche-boosted)")
        result["topic"] = topic.title

        # 2. SCRIPT
        self.logger.info("\n[2/9] Script...")
        if script_file:
            # Build Script directly from the parsed file — no LLM call needed
            script = _build_script_from_parsed(parsed)
            self.logger.info(f"  [from file] '{script.title}' ({script.total_words} words)")
        else:
            writer = ScriptWriter(provider=self.cfg.llm_provider)
            if is_reel:
                script = writer.generate(topic=topic, video_length=1, tone="casual_fun", niche=niche)
            else:
                script = writer.generate(topic=topic, video_length=self.cfg.video_length_minutes,
                    tone=self.cfg.tone, niche=niche)
            if researcher:
                researcher.mark_used(topic)
        result["title"] = script.title
        result["words"] = script.total_words
        self.logger.info(f"  '{script.title}' ({script.total_words} words)")

        # Archive
        with open(os.path.join(self.cfg.scripts_dir, f"{vid_id}_script.json"), "w") as f:
            json.dump({"topic":asdict(topic),"title":script.title,
                "scenes":[asdict(s) for s in script.scenes],"tags":script.tags,
                "description_seo":script.description_seo,"thumbnail_concept":script.thumbnail_concept}, f, indent=2)

        if dry_run:
            result["status"] = "DRY_RUN"; return result

        # 3. SINGLE TTS AUDIO
        self.logger.info("\n[3/9] TTS (single audio)...")
        narration  = " ".join(s.narration for s in script.scenes if s.narration.strip())
        audio_path = os.path.join(work_dir, "narration.mp3")
        _cache_dir  = os.path.join("cache"); os.makedirs(_cache_dir, exist_ok=True)
        _cached_mp3 = os.path.join(_cache_dir, "last_narration.mp3")
        _reuse      = getattr(self, "_reuse_audio", False)
        if _reuse and os.path.exists(_cached_mp3):
            shutil.copy2(_cached_mp3, audio_path)
            self.logger.info(f"  [reuse-audio] Skipped ElevenLabs — using cached: {_cached_mp3}")
        else:
            if _reuse:
                self.logger.warning("  [reuse-audio] No cache found — generating fresh audio")
            TTSEngine(self.cfg.tts_provider, self.cfg.tts_voice_id, self.cfg.tts_model).generate_full_audio(narration, audio_path)
            shutil.copy2(audio_path, _cached_mp3)  # always save latest for next --reuse-audio run
            self.logger.info(f"  Cached audio → {_cached_mp3}")

        # Measure actual audio duration so clip count and speed-up can be decided now
        import math as _math, subprocess as _sp
        _raw_dur = float(_sp.run(
            ["ffprobe","-v","quiet","-show_entries","format=duration","-of","csv=p=0", audio_path],
            capture_output=True, text=True).stdout.strip() or "0")

        # Pre-speed audio BEFORE subtitle generation so Whisper timestamps stay in sync.
        # If audio > 89s we speed it up now; Whisper then runs on the already-compressed
        # audio, so karaoke timestamps match the final video exactly.
        _reel_max = 89
        audio_dur = _raw_dur
        if is_reel and _raw_dur > _reel_max:
            _speed = _raw_dur / _reel_max
            _sped_path = os.path.join(work_dir, "narration_sped.mp3")
            self.logger.info(f"  Audio {_raw_dur:.1f}s > {_reel_max}s — pre-speeding {_speed:.2f}x (subtitle sync)")
            _chain = []
            _rem = _speed
            while _rem > 2.0:
                _chain.append("atempo=2.0"); _rem /= 2.0
            _chain.append(f"atempo={_rem:.4f}")
            _sp.run(["ffmpeg", "-y", "-i", audio_path, "-filter:a", ",".join(_chain),
                     "-c:a", "libmp3lame", "-q:a", "2", _sped_path],
                    capture_output=True, check=True)
            audio_path = _sped_path
            audio_dur  = _reel_max

        # 4. IMAGE PROMPTS — clip count based on (post-speed) audio duration
        orientation = "portrait" if is_reel else "landscape"
        if is_reel:
            # 1 clip per ~5s of audio; min 8, max 20
            num_images = max(8, min(20, _math.ceil(audio_dur / 5.0)))
        else:
            num_images = self.cfg.images_longform
        self.logger.info(f"\n[4/9] {num_images} image prompts ({orientation}, {audio_dur:.0f}s audio)...")
        prompts = generate_image_prompts(narration, num_images, orientation, self.cfg.llm_provider)

        # 5. FETCH MEDIA (images or video clips)
        _use_vids   = getattr(self, "_use_videos", False)
        _vintage    = getattr(self, "_vintage", False) or niche == "heritage"
        media_dir   = os.path.join(work_dir, "media")
        # Heritage niche: steer Pexels toward ancient/historical footage
        _search_mod = "ancient historical" if niche == "heritage" else ""
        if _use_vids:
            self.logger.info(f"\n[5/9] Fetching {num_images} video clips (Pexels)...")
            image_paths = fetch_pexels_videos(prompts, media_dir, orientation,
                                              search_modifier=_search_mod)
        else:
            self.logger.info(f"\n[5/9] Fetching {num_images} images...")
            image_paths = fetch_pexels_images(prompts, media_dir, orientation)

        # 6. ASS SUBTITLES (with keyword highlighting)
        self.logger.info("\n[6/9] Subtitles (with keyword highlights)...")
        dur = audio_dur  # post-speed duration — keeps Whisper timestamps in sync
        sub_width  = 1080 if is_reel else self.cfg.video_width
        sub_height = 1920 if is_reel else self.cfg.video_height
        sub_font   = 84   if is_reel else 64   # reels: 1080x1920, long-form: 1920x1080
        sub_margin = 150  if is_reel else 80
        ass_path = os.path.join(work_dir, "subtitles.ass")
        hook_duration = 0.0   # subtitles start from the first word
        # generate_ass_whisper_with_highlights handles all fallbacks internally:
        #   faster-whisper → openai-whisper → text-based ASS
        # narration_text + audio_duration enable the text-based fallback if
        # both Whisper backends fail (e.g. DLL errors on Windows).
        self.logger.info("  Subtitles: karaoke (Whisper small) with text fallback���")
        generate_ass_whisper_with_highlights(
            audio_path, ass_path,
            font_size=sub_font, margin_v=sub_margin,
            width=sub_width, height=sub_height,
            hook_duration=hook_duration,
            model_size="small",
            narration_text=narration,
            audio_duration=dur,
        )

        # 7. ASSEMBLE (SLIDESHOW — music first, then subs with Windows fix)
        self.logger.info("\n[7/9] Assembling slideshow...")
        bg_music = self._find_bg_music()
        slideshow_cfg = SlideshowConfig(
            width=1080 if is_reel else self.cfg.video_width,
            height=1920 if is_reel else self.cfg.video_height,
            fps=self.cfg.video_fps, crf=self.cfg.video_crf, preset=self.cfg.video_preset,
            subtitle_font_size=64,
            subtitle_margin_bottom=80,
            reel_subtitle_font_size=84,
            reel_subtitle_margin_bottom=150,
            bg_music_volume=self.cfg.bg_music_volume,
            reel_bg_music_volume=0.12,
            reel_max_duration=89 if is_reel else 9999,
            temp_dir=work_dir, output_dir=self.cfg.output_dir,
            vintage_effect=_vintage,
        )
        if _vintage:
            self.logger.info("  Vintage film effect: ON (old-footage look)")
        assembler = SlideshowAssembler(slideshow_cfg)
        output_filename = f"{vid_id}.mp4"
        # Heritage: show the episode title as the hook banner (not a click-bait question)
        # Other reels: use LLM hook_question (≤40 chars scroll-stopper)
        if niche == "heritage":
            hook_overlay = script.title
        else:
            hook_overlay = (script.hook_question or script.title) if is_reel else script.title
        if is_reel:
            video_path = assembler.assemble_reel(audio_path, image_paths, output_filename,
                                                  ass_path, bg_music, hook_text=hook_overlay)
        else:
            video_path = assembler.assemble(audio_path, image_paths, output_filename,
                                            ass_path, bg_music, hook_text=hook_overlay)
        result["video_path"] = video_path
        result["size_mb"] = round(os.path.getsize(video_path)/(1024*1024), 1)

        # 8. THUMBNAIL
        self.logger.info("\n[8/9] Thumbnail...")
        thumb_path = os.path.join(self.cfg.output_dir, f"{vid_id}_thumb.jpg")
        try: assembler.generate_thumbnail(image_paths[0], script.title, thumb_path, vertical=is_reel)
        except: thumb_path = None

        # 9. UPLOAD
        self.logger.info("\n[9/9] Upload...")
        desc = self._build_desc(script, is_reel)
        if is_reel:
            result["uploads"] = MultiPlatformUploader(self.cfg).upload_reel(
                video_path, script.title, desc, script.tags, thumb_path)
        elif self.cfg.auto_upload:
            up = YouTubeUploader(UploaderConfig(slack_webhook_url=self.cfg.slack_webhook,
                telegram_bot_token=self.cfg.telegram_token, telegram_chat_id=self.cfg.telegram_chat))
            up.authenticate()
            pt = PublishScheduler.get_next_publish_time(self.cfg.schedule_timezone, self.cfg.schedule_days_ahead+idx)
            r = up.upload(video_path=video_path, metadata=VideoMetadata(title=script.title, description=desc,
                tags=script.tags, category_id=NICHE_CONFIG.get(niche,{}).get("youtube_category","28"),
                privacy_status=self.cfg.default_privacy,
                publish_at=pt if self.cfg.default_privacy=="private" else None,
                thumbnail_path=thumb_path, playlist_id=self.cfg.playlist_id))
            result["video_url"], result["scheduled"] = r.video_url, pt
        else:
            self.logger.info(f"  Upload skipped. Video: {video_path}")

        result["status"] = "SUCCESS"
        # Cleanup
        #if os.path.exists(work_dir): shutil.rmtree(work_dir, ignore_errors=True)
        return result

    def _find_bg_music(self):
        d = self.cfg.bg_music_dir
        if not os.path.exists(d): return None
        t = [f for f in os.listdir(d) if f.endswith((".mp3",".wav",".m4a"))]
        return os.path.join(d, random.choice(t)) if t else None

    def _build_desc(self, script, is_reel):
        parts = [script.description_seo or "", ""]
        if not is_reel:
            c = 0.0
            parts.append("TIMESTAMPS:")
            for s in script.scenes:
                m, sc = int(c//60), int(c%60)
                parts.append(f"{m}:{sc:02d} {(s.on_screen_text or s.scene_type.replace('_',' ').title())[:50]}")
                c += s.duration_estimate
            parts.append("")
        if script.tags:
            parts.append(" ".join(f"#{t.replace(' ','')}" for t in script.tags[:5]))
        parts += ["", "Subscribe for more! | FutureProof AI"]
        return "\n".join(parts)

    def retry_upload(self, video_path: str, title: str = "", platforms: str = "all"):
        """
        Re-upload an already-generated video without re-running the pipeline.
        Reads title from filename if not provided.
        platforms: 'all' | comma-separated subset e.g. 'instagram,youtube'
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video not found: {video_path}")

        title = title or os.path.splitext(os.path.basename(video_path))[0]
        desc  = "Subscribe for more! | FutureProof AI"
        tags  = []

        # Try to read title/tags from archived script JSON if available
        vid_id     = os.path.splitext(os.path.basename(video_path))[0]
        script_json = os.path.join(self.cfg.scripts_dir, f"{vid_id}_script.json")
        if os.path.exists(script_json):
            try:
                with open(script_json) as f:
                    data  = json.load(f)
                title = data.get("title", title)
                tags  = data.get("tags", [])
                desc  = data.get("description_seo", desc) + "\n\nSubscribe for more! | FutureProof AI"
                self.logger.info(f"  Loaded metadata from {script_json}")
            except Exception as e:
                self.logger.warning(f"  Could not read script JSON: {e}")

        self.logger.info(f"  Retrying upload: {video_path}")
        self.logger.info(f"  Title: {title}")

        # Override platform flags if user restricted them
        cfg = self.cfg
        if platforms != "all":
            selected = {p.strip().lower() for p in platforms.split(",")}
            cfg = type("Cfg", (), {k: getattr(self.cfg, k) for k in dir(self.cfg)
                                   if not k.startswith("__")})()
            cfg.reels_instagram = "instagram" in selected
            cfg.reels_youtube   = "youtube"   in selected
            cfg.reels_facebook  = "facebook"  in selected

        uploader = MultiPlatformUploader(cfg)

        # Find thumbnail if it exists alongside the video
        thumb = os.path.join(os.path.dirname(video_path), f"{vid_id}_thumb.jpg")
        thumb = thumb if os.path.exists(thumb) else None

        results = uploader.upload_reel(video_path, title, desc, tags, thumb)
        self.logger.info(f"  Upload results: {results}")
        return results

    @staticmethod
    def list_outputs(output_dir: str):
        """Print all generated videos in the output directory."""
        videos = sorted(
            [f for f in os.listdir(output_dir) if f.endswith(".mp4")],
            key=lambda f: os.path.getmtime(os.path.join(output_dir, f)),
            reverse=True,
        )
        if not videos:
            print("  No videos found in output/")
            return
        print(f"\n  {'#':<4} {'File':<50} {'Size':>8}  {'Modified'}")
        print(f"  {'-'*80}")
        for i, f in enumerate(videos, 1):
            fp   = os.path.join(output_dir, f)
            size = os.path.getsize(fp) / (1024 * 1024)
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M")
            print(f"  {i:<4} {f:<50} {size:>6.1f}MB  {mtime}")
        print()


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="FutureProof AI — Video Pipeline", epilog="""
  python orchestrator.py --niche ai_tools --count 1
  python orchestrator.py --reels --niche ai_tools --count 3
  python orchestrator.py --reels --multi-niche ai_tools,finance,tech
  python orchestrator.py --dry-run --niche ai_tools
  python orchestrator.py --discover-only 5""")
    p.add_argument("--niche", type=str, choices=list(NICHE_CONFIG.keys()))
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--length", type=int)
    p.add_argument("--tone", type=str)
    p.add_argument("--provider", type=str, choices=["anthropic","openai","gemini"])
    p.add_argument("--tts-provider", type=str, choices=["elevenlabs","openai","minimax"],
                   dest="tts_provider", help="TTS engine (overrides TTS_PROVIDER in .env)")
    p.add_argument("--upload", action="store_true")
    p.add_argument("--privacy", type=str, choices=["public","private","unlisted"])
    p.add_argument("--reels", action="store_true")
    p.add_argument("--multi-niche", type=str)
    p.add_argument("--topic", type=str, metavar="TOPIC",
                   help="Skip topic discovery — create a video on this exact topic")
    p.add_argument("--script-file", type=str, metavar="PATH", dest="script_file",
                   help="Path to a structured script file ([TITLE]/[HOOK]/[BODY]/[CLOSER]/[HASHTAGS]). "
                        "Skips topic discovery AND script generation entirely.")
    p.add_argument("--retry-upload", type=str, metavar="VIDEO_PATH",
                   help="Re-upload an existing video (skips all generation steps)")
    p.add_argument("--title", type=str, default="",
                   help="Title override for --retry-upload (auto-read from script JSON if omitted)")
    p.add_argument("--platforms", type=str, default="all",
                   help="Platforms for --retry-upload: all | instagram,youtube,facebook")
    p.add_argument("--list-outputs", action="store_true",
                   help="List all generated videos in the output folder")
    p.add_argument("--use-videos", action="store_true", dest="use_videos",
                   help="Use Pexels video clips instead of images (more dynamic reels)")
    p.add_argument("--vintage", action="store_true",
                   help="Apply old-film/ancient-footage look (grain, sepia, vignette). "
                        "Auto-enabled for --niche heritage.")
    p.add_argument("--reuse-audio", action="store_true", dest="reuse_audio",
                   help="Skip ElevenLabs and reuse the last generated audio (cache/last_narration.mp3). "
                        "Saves API tokens when retrying after pipeline failures.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--discover-only", type=int, metavar="N")
    p.add_argument("--refresh-analytics", action="store_true",
                   help="Pull latest Instagram reel stats and update the performance DB")
    p.add_argument("--analytics-report", action="store_true",
                   help="Show niche performance report from stored analytics")
    p.add_argument("--no-analytics", action="store_true",
                   help="Disable analytics-weighted niche selection for this run")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)-16s] %(levelname)-7s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
            logging.FileHandler(f"logs/pipeline_{datetime.date.today().isoformat()}.log", encoding="utf-8")])

    cfg = PipelineConfig()
    cfg.apply_overrides(args)
    orch = PipelineOrchestrator(cfg)
    orch._use_videos   = getattr(args, "use_videos", False)
    orch._vintage      = getattr(args, "vintage", False)
    orch._reuse_audio  = getattr(args, "reuse_audio", False)
    db_path = os.path.join(cfg.data_dir, "topics.db")

    if args.list_outputs:
        PipelineOrchestrator.list_outputs(cfg.output_dir)
        return

    if args.retry_upload:
        results = orch.retry_upload(args.retry_upload,
                                    title=args.title,
                                    platforms=args.platforms)
        print(f"\n  Upload results:")
        for platform, info in results.items():
            print(f"    {platform}: {info.get('url', info.get('error', 'N/A'))}")
        return

    if args.refresh_analytics:
        n = refresh_instagram_analytics(db_path, NICHE_CONFIG)
        print(f"\n  Refreshed {n} reels from Instagram.")
        print_performance_report(db_path)
        if not (args.reels or args.discover_only or args.analytics_report):
            return

    if args.analytics_report:
        print_performance_report(db_path)
        return

    if args.discover_only:
        topics = TopicResearcher(niche=cfg.niche, db_path=os.path.join(cfg.data_dir,"topics.db")).discover(count=args.discover_only)
        print(f"\n{'='*60}\nTop {len(topics)} — {cfg.niche}\n{'='*60}")
        for i, t in enumerate(topics, 1): print(f"  {i}. [{t.score:.0f}] {t.title} ({t.source})")
        return

    use_analytics = not args.no_analytics
    forced_topic  = args.topic or None
    script_file   = getattr(args, "script_file", None)

    # Validate --script-file path early
    if script_file:
        if not os.path.exists(script_file):
            print(f"  ERROR: --script-file not found: {script_file}")
            return
        # --script-file implies single video; ignore --count > 1 with a warning
        if args.count > 1:
            print(f"  NOTE: --script-file uses a single fixed script — running count=1")
            args.count = 1

    results = []
    if not args.reels:
        results.extend(orch.run(mode="longform", count=args.count,
                                dry_run=args.dry_run, use_analytics=use_analytics,
                                forced_topic=forced_topic, script_file=script_file))
    if args.reels:
        niches = [n.strip() for n in args.multi_niche.split(",")] if args.multi_niche else None
        cnt = len(niches) if niches else args.count
        results.extend(orch.run(mode="reels", count=cnt, dry_run=args.dry_run,
                                niches=niches, use_analytics=use_analytics,
                                forced_topic=forced_topic, script_file=script_file))

    print(f"\n{'='*60}\nRESULTS\n{'='*60}")
    for r in results:
        s = {"SUCCESS":"OK","DRY_RUN":"DRY","FAILED":"FAIL"}.get(r.get("status","?"),"?")
        print(f"\n  [{s}] {r.get('type','?').upper()} #{r.get('num','?')}")
        print(f"       {r.get('title','N/A')}")
        if r.get("video_url"): print(f"       YT: {r['video_url']}")
        if r.get("uploads"):
            for pl, info in r["uploads"].items(): print(f"       {pl}: {info.get('url',info.get('error','N/A'))}")
        if r.get("error"): print(f"       Error: {r['error']}")

if __name__ == "__main__": main()
