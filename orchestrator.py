"""
UNIFIED ORCHESTRATOR v4 — Long-Form + Reels Pipeline (ALL FIXES)
=================================================================
  Long-form:  python orchestrator.py --niche ai_tools --count 1
  Reels:      python orchestrator.py --reels --niche ai_tools --count 3
  Both:       python orchestrator.py --niche ai_tools --count 1 && python orchestrator.py --reels --count 2

Uses slideshow_assembler v4 (subtitle fix, correct font sizes, music before subs).
"""

import os, sys, json, time, shutil, random, logging, datetime, argparse, traceback
from typing import List, Dict, Optional
from dataclasses import asdict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError: pass

from topic_and_script import TopicResearcher, ScriptWriter, Topic, Script, ScriptScene, NICHE_CONFIG
from slideshow_assembler import (SlideshowAssembler, SlideshowConfig, generate_image_prompts,
    fetch_pexels_images, generate_ass_with_highlights, generate_ass_whisper_with_highlights)
from youtube_uploader import (UploaderConfig, YouTubeUploader, VideoMetadata,
    PublishScheduler, QuotaExceededError)


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
        self.logger.info(f"  TTS: {len(full_text)} chars → {output_path}")
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
        self.logger.info(f"  TTS complete")
        return output_path


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
        sp = os.path.join("config","ig_session.json")
        if os.path.exists(sp): cl.load_settings(sp); cl.login(self.cfg.ig_username, self.cfg.ig_password)
        else: cl.login(self.cfg.ig_username, self.cfg.ig_password); cl.dump_settings(sp)
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
#  ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════

class PipelineOrchestrator:
    def __init__(self, cfg):
        self.cfg, self.logger = cfg, logging.getLogger("Orchestrator")
        for d in [cfg.output_dir, cfg.temp_dir, cfg.logs_dir, cfg.data_dir,
                  cfg.scripts_dir, cfg.assets_dir, os.path.join(cfg.assets_dir,"music")]:
            os.makedirs(d, exist_ok=True)

    def run(self, mode="longform", count=1, dry_run=False, niches=None):
        run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        niche_list = niches or [self.cfg.niche]
        self.logger.info(f"\n{'='*70}\n  PIPELINE {run_id} | {mode} | {count} videos\n{'='*70}\n")
        results, start = [], time.time()
        for i in range(count):
            niche = niche_list[i % len(niche_list)]
            is_reel = mode in ("reels","reel")
            self.logger.info(f"\n{'─'*50}\n  {'REEL' if is_reel else 'VIDEO'} {i+1}/{count} [{niche}]\n{'─'*50}")
            try:
                results.append(self._produce(run_id, i, niche, is_reel, dry_run))
            except QuotaExceededError as e:
                self.logger.error(f"  Quota: {e}"); break
            except Exception as e:
                self.logger.error(f"  Failed: {e}"); self.logger.debug(traceback.format_exc())
                results.append({"num":i+1,"status":"FAILED","error":str(e)})
            if i < count-1 and not dry_run: time.sleep(10)
        ok = sum(1 for r in results if r.get("status")=="SUCCESS")
        self.logger.info(f"\nDONE: {ok}/{count} in {(time.time()-start)/60:.1f} min")
        return results

    def _produce(self, run_id, idx, niche, is_reel, dry_run):
        vid_id = f"{run_id}_{'reel' if is_reel else 'vid'}{idx:02d}"
        work_dir = os.path.join(self.cfg.temp_dir, vid_id)
        os.makedirs(os.path.join(work_dir,"images"), exist_ok=True)
        result = {"num":idx+1,"type":"reel" if is_reel else "longform","status":"STARTED"}

        # 1. TOPIC
        self.logger.info("\n[1/9] Topic...")
        researcher = TopicResearcher(niche=niche, db_path=os.path.join(self.cfg.data_dir,"topics.db"))
        topics = researcher.discover(count=1)
        if not topics: raise RuntimeError("No topics")
        topic = topics[0]
        result["topic"] = topic.title
        self.logger.info(f"  [{topic.score:.0f}] {topic.title}")

        # 2. SCRIPT
        self.logger.info("\n[2/9] Script...")
        writer = ScriptWriter(provider=self.cfg.llm_provider)
        if is_reel:
            script = writer.generate(topic=topic, video_length=1, tone="casual_fun", niche=niche)
        else:
            script = writer.generate(topic=topic, video_length=self.cfg.video_length_minutes,
                tone=self.cfg.tone, niche=niche)
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
        narration = " ".join(s.narration for s in script.scenes if s.narration.strip())
        audio_path = os.path.join(work_dir, "narration.mp3")
        TTSEngine(self.cfg.tts_provider, self.cfg.tts_voice_id, self.cfg.tts_model).generate_full_audio(narration, audio_path)

        # 4. IMAGE PROMPTS
        num_images = self.cfg.images_reel if is_reel else self.cfg.images_longform
        orientation = "portrait" if is_reel else "landscape"
        self.logger.info(f"\n[4/9] {num_images} image prompts ({orientation})...")
        prompts = generate_image_prompts(narration, num_images, orientation, self.cfg.llm_provider)

        # 5. FETCH IMAGES
        self.logger.info(f"\n[5/9] Fetching {num_images} images...")
        image_paths = fetch_pexels_images(prompts, os.path.join(work_dir,"images"), orientation)

        # 6. ASS SUBTITLES (with keyword highlighting)
        self.logger.info("\n[6/9] Subtitles (with keyword highlights)...")
        import subprocess
        dur = float(subprocess.run(["ffprobe","-v","quiet","-show_entries","format=duration",
            "-of","csv=p=0",audio_path], capture_output=True, text=True).stdout.strip() or "0")
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
        self.logger.info("  Subtitles: karaoke (Whisper small) with text fallback…")
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
            reel_max_duration=59 if is_reel else 9999,
            temp_dir=work_dir, output_dir=self.cfg.output_dir,
        )
        assembler = SlideshowAssembler(slideshow_cfg)
        output_filename = f"{vid_id}.mp4"
        if is_reel:
            video_path = assembler.assemble_reel(audio_path, image_paths, output_filename,
                                                  ass_path, bg_music, hook_text=script.title)
        else:
            video_path = assembler.assemble(audio_path, image_paths, output_filename,
                                            ass_path, bg_music, hook_text=script.title)
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
    p.add_argument("--upload", action="store_true")
    p.add_argument("--privacy", type=str, choices=["public","private","unlisted"])
    p.add_argument("--reels", action="store_true")
    p.add_argument("--multi-niche", type=str)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--discover-only", type=int, metavar="N")
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

    if args.discover_only:
        topics = TopicResearcher(niche=cfg.niche, db_path=os.path.join(cfg.data_dir,"topics.db")).discover(count=args.discover_only)
        print(f"\n{'='*60}\nTop {len(topics)} — {cfg.niche}\n{'='*60}")
        for i, t in enumerate(topics, 1): print(f"  {i}. [{t.score:.0f}] {t.title} ({t.source})")
        return

    results = []
    if not args.reels:
        results.extend(orch.run(mode="longform", count=args.count, dry_run=args.dry_run))
    if args.reels:
        niches = [n.strip() for n in args.multi_niche.split(",")] if args.multi_niche else None
        cnt = len(niches) if niches else args.count
        results.extend(orch.run(mode="reels", count=cnt, dry_run=args.dry_run, niches=niches))

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
