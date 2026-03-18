"""
SLIDESHOW VIDEO ASSEMBLER v4 — Long-Form + Reels (ALL FIXES APPLIED)
=====================================================================
Unified assembler for BOTH formats:
  Long-form: 1920x1080, 20+ images, 8-12 min
  Reels:     1080x1920, 8-12 images, 30-60 sec

Both use: 1 audio + multiple images → slideshow → subtitles → music → done
No scene concatenation. No concat bugs.

FIXES in v4:
  - Subtitles: copy files to same dir before burn (Windows path fix)
  - Order: music FIRST, then subtitles (matches working manual command)
  - Font sizes: 42 for Reels, 28 for long-form
  - MarginV: 100 for Reels, 60 for long-form
"""

import os
import re
import sys
import math
import random
import shutil
import logging
import subprocess
import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════

@dataclass
class SlideshowConfig:

    # Long-form defaults
    width: int = 1920
    height: int = 1080
    fps: int = 30
    codec: str = "libx264"
    preset: str = "medium"
    crf: int = 20
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"

    # Image timing (long-form)
    seconds_per_image: float = 10.0
    min_seconds_per_image: float = 7.0
    max_seconds_per_image: float = 15.0
    randomize_duration: bool = True

    # Image timing (reels)
    reel_seconds_per_image: float = 5.0
    reel_min_seconds: float = 3.0
    reel_max_seconds: float = 8.0
    reel_max_duration: int = 59

    # Effects
    effects: List[str] = field(default_factory=lambda: [
        "kenburns", "zoom_in", "zoom_out",
        "pan_left_to_right", "pan_right_to_left",
        "static_breathe",
    ])
    reel_effects: List[str] = field(default_factory=lambda: [
        "zoom_in", "zoom_out", "static_breathe", "kenburns",
    ])
    cycle_effects: bool = True

    # Subtitles (long-form)
    subtitle_font_size: int = 28
    subtitle_margin_bottom: int = 60
    subtitle_outline: int = 2
    subtitle_shadow: int = 2

    # Subtitles (reels)
    reel_subtitle_font_size: int = 42
    reel_subtitle_margin_bottom: int = 100

    # Background music
    bg_music_volume: float = 0.08
    bg_music_fade_in: float = 2.0
    bg_music_fade_out: float = 3.0
    reel_bg_music_volume: float = 0.12

    # Watermark
    watermark_path: Optional[str] = None
    watermark_opacity: float = 0.3
    watermark_position: str = "top-right"

    # Paths
    temp_dir: str = "tmp/slideshow"
    output_dir: str = "output"


# ═══════════════════════════════════════════════════════════════
#  CORE ASSEMBLER
# ═══════════════════════════════════════════════════════════════

class SlideshowAssembler:

    def __init__(self, config: SlideshowConfig = None):
        self.cfg = config or SlideshowConfig()
        self.logger = logging.getLogger("SlideshowAssembler")
        os.makedirs(self.cfg.temp_dir, exist_ok=True)
        os.makedirs(self.cfg.output_dir, exist_ok=True)

    # ─── LONG-FORM ────────────────────────────────────────────────

    def assemble(self, audio_path, image_paths, output_path,
                 srt_path=None, bg_music_path=None):
        self.logger.info(f"\n  LONG-FORM: {len(image_paths)} images + 1 audio")
        return self._render_video(
            audio_path=audio_path, image_paths=image_paths,
            output_path=output_path, srt_path=srt_path, bg_music_path=bg_music_path,
            width=self.cfg.width, height=self.cfg.height,
            min_sec=self.cfg.min_seconds_per_image, max_sec=self.cfg.max_seconds_per_image,
            effects_list=self.cfg.effects,
            sub_font_size=self.cfg.subtitle_font_size,
            sub_margin=self.cfg.subtitle_margin_bottom,
            music_vol=self.cfg.bg_music_volume,
            max_duration=None,
        )

    # ─── REELS / SHORTS ──────────────────────────────────────────

    def assemble_reel(self, audio_path, image_paths, output_path,
                      srt_path=None, bg_music_path=None):
        self.logger.info(f"\n  REEL: {len(image_paths)} images + 1 audio")
        return self._render_video(
            audio_path=audio_path, image_paths=image_paths,
            output_path=output_path, srt_path=srt_path, bg_music_path=bg_music_path,
            width=1080, height=1920,
            min_sec=self.cfg.reel_min_seconds, max_sec=self.cfg.reel_max_seconds,
            effects_list=self.cfg.reel_effects,
            sub_font_size=self.cfg.reel_subtitle_font_size,
            sub_margin=self.cfg.reel_subtitle_margin_bottom,
            music_vol=self.cfg.reel_bg_music_volume,
            max_duration=self.cfg.reel_max_duration,
        )

    # ─── SHARED RENDERING ENGINE ──────────────────────────────────

    def _render_video(self, audio_path, image_paths, output_path, srt_path,
                      bg_music_path, width, height, min_sec, max_sec,
                      effects_list, sub_font_size, sub_margin, music_vol,
                      max_duration):

        # Step 1: Audio duration
        audio_duration = self._get_duration(audio_path)
        if max_duration and audio_duration > max_duration:
            audio_duration = max_duration
        self.logger.info(f"  Audio: {audio_duration:.1f}s | Res: {width}x{height}")

        # Step 2: Image durations
        durations = self._calc_durations(len(image_paths), audio_duration, min_sec, max_sec)
        self.logger.info(f"  Images: {len(durations)}, avg {sum(durations)/len(durations):.1f}s each")

        # Step 3: Assign effects
        effects = self._assign_effects(len(image_paths), effects_list)

        # Step 4: Render image clips
        self.logger.info("  Rendering image clips...")
        clip_paths = []
        for i, (img, dur, eff) in enumerate(zip(image_paths, durations, effects)):
            clip = os.path.join(self.cfg.temp_dir, f"clip_{i:03d}.mp4")
            self._render_clip(img, clip, dur, eff, width, height)
            clip_paths.append(clip)
            if (i + 1) % 5 == 0 or i == len(image_paths) - 1:
                self.logger.info(f"    {i+1}/{len(image_paths)} clips done")

        # Step 5: Combine clips + audio
        self.logger.info("  Combining slideshow + audio...")
        raw = os.path.join(self.cfg.temp_dir, "raw.mp4")
        self._combine(clip_paths, audio_path, raw, audio_duration)

        # Step 6: Mix background music FIRST
        current = raw
        if bg_music_path and os.path.exists(bg_music_path):
            self.logger.info("  Mixing background music...")
            mus_out = os.path.join(self.cfg.temp_dir, "with_music.mp4")
            self._mix_music(current, bg_music_path, mus_out, audio_duration, music_vol)
            current = mus_out

        # Step 7: Burn subtitles AFTER music (Windows path fix applied)
        if srt_path and os.path.exists(srt_path):
            self.logger.info("  Burning subtitles...")
            sub_out = os.path.join(self.cfg.temp_dir, "subtitled.mp4")
            self._burn_subs(current, srt_path, sub_out, sub_font_size, sub_margin)
            current = sub_out

        # Step 8: Watermark
        if self.cfg.watermark_path and os.path.exists(self.cfg.watermark_path):
            self.logger.info("  Adding watermark...")
            wm_out = os.path.join(self.cfg.temp_dir, "watermarked.mp4")
            self._add_watermark(current, wm_out)
            current = wm_out

        # Step 9: Trim if over max duration
        if max_duration:
            actual_dur = self._get_duration(current)
            if actual_dur > max_duration:
                self.logger.info(f"  Trimming {actual_dur:.0f}s → {max_duration}s...")
                trimmed = os.path.join(self.cfg.temp_dir, "trimmed.mp4")
                self._ffmpeg(["ffmpeg", "-y", "-i", current, "-t", str(max_duration),
                             "-c:v", "copy", "-c:a", "copy", trimmed])
                current = trimmed

        # Step 10: Copy to output
        final = os.path.join(self.cfg.output_dir, os.path.basename(output_path))
        self._ffmpeg(["ffmpeg", "-y", "-i", current, "-c", "copy",
                      "-movflags", "+faststart", final])

        size_mb = os.path.getsize(final) / (1024 * 1024)
        duration = self._get_duration(final)
        self.logger.info(f"  DONE: {final} ({size_mb:.1f} MB, {duration:.0f}s)")
        return final

    # ─── DURATION CALCULATION ─────────────────────────────────────

    def _calc_durations(self, count, total, min_s, max_s):
        base = max(min_s, min(total / count, max_s))
        durations = []
        remaining = total
        for i in range(count):
            if i == count - 1:
                dur = max(min_s, remaining)
            elif self.cfg.randomize_duration:
                v = base * 0.25
                dur = max(min_s, min(base + random.uniform(-v, v), max_s))
            else:
                dur = base
            durations.append(dur)
            remaining -= dur
        scale = total / sum(durations) if sum(durations) > 0 else 1
        return [d * scale for d in durations]

    # ─── EFFECT ASSIGNMENT ────────────────────────────────────────

    def _assign_effects(self, count, effects_list):
        result, last = [], None
        for _ in range(count):
            choices = [e for e in effects_list if e != last] or effects_list
            chosen = random.choice(choices)
            result.append(chosen)
            last = chosen
        return result

    # ─── RENDER SINGLE CLIP ──────────────────────────────────────

    def _render_clip(self, img, out, dur, effect, w, h):
        fps = self.cfg.fps
        frames = int(dur * fps) + fps
        vf = self._effect_filter(effect, w, h, dur + 1, fps, frames)
        self._ffmpeg([
            "ffmpeg", "-y", "-loop", "1", "-framerate", str(fps),
            "-t", str(dur + 0.5), "-i", img,
            "-vf", vf, "-c:v", self.cfg.codec, "-preset", "fast",
            "-crf", str(self.cfg.crf + 2), "-t", str(dur),
            "-an", "-pix_fmt", "yuv420p", out
        ])

    def _effect_filter(self, effect, w, h, dur, fps, frames):
        f = {
            "kenburns": f"scale=8000:-1,zoompan=z='min(zoom+0.0015,1.5)':x='if(gte(zoom,1.5),x,x+1)':y='if(gte(zoom,1.5),y,y+0.5)':d={frames}:s={w}x{h}:fps={fps}",
            "zoom_in": f"scale=8000:-1,zoompan=z='min(zoom+0.001,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}",
            "zoom_out": f"scale=8000:-1,zoompan=z='if(eq(on,1),1.5,max(zoom-0.002,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}",
            "pan_left_to_right": f"scale=-1:{h*2},zoompan=z='1.2':x='(iw-iw/zoom)*on/{frames}':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}",
            "pan_right_to_left": f"scale=-1:{h*2},zoompan=z='1.2':x='(iw-iw/zoom)*(1-on/{frames})':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}",
            "static_breathe": f"scale=8000:-1,zoompan=z='1.05+0.02*sin(2*PI*on/{frames})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps}",
            "static": f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black",
        }
        return f.get(effect, f["kenburns"])

    # ─── COMBINE CLIPS + AUDIO ────────────────────────────────────

    def _combine(self, clips, audio, out, duration):
        concat_file = os.path.join(self.cfg.temp_dir, "concat.txt")
        with open(concat_file, "w", encoding="utf-8") as f:
            for p in clips:
                f.write(f"file '{os.path.abspath(p).replace(chr(92), '/')}'\n")
        self._ffmpeg([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
            "-i", audio, "-map", "0:v", "-map", "1:a",
            "-c:v", self.cfg.codec, "-preset", self.cfg.preset, "-crf", str(self.cfg.crf),
            "-c:a", self.cfg.audio_codec, "-b:a", self.cfg.audio_bitrate,
            "-t", str(duration), "-pix_fmt", "yuv420p", "-shortest",
            "-movflags", "+faststart", out
        ])

    # ─── SUBTITLES (WINDOWS FIX: copy to same dir, use relative paths) ────

    def _burn_subs(self, inp, srt, out, font_size, margin):
        """
        Burn subtitles into video. Supports both .srt and .ass files.

        .srt → uses subtitles filter with force_style (plain white text)
        .ass → uses subtitles filter without force_style; colour/style come
               from the ASS file itself (enables per-word keyword highlighting)

        Uses same-directory trick for Windows path compatibility.
        """
        is_ass = srt.lower().endswith(".ass")
        sub_filename = "subs.ass" if is_ass else "subs.srt"

        # SRT-only style override (ignored for ASS — styles are embedded)
        style = (
            f"FontSize={font_size},"
            f"PrimaryColour=&H00FFFFFF,"
            f"OutlineColour=&H00000000,"
            f"Bold=1,"
            f"Outline={self.cfg.subtitle_outline},"
            f"Shadow={self.cfg.subtitle_shadow},"
            f"MarginL=40,"
            f"MarginR=40,"
            f"MarginV=0,"
            f"Alignment=5,"
            f"WrapStyle=1"
        )

        # Verify subtitle file exists and has content
        if not os.path.exists(srt):
            self.logger.error(f"    Subtitle file not found: {srt}")
            self._ffmpeg(["ffmpeg", "-y", "-i", inp, "-c", "copy", out])
            return

        with open(srt, "r", encoding="utf-8") as f:
            sub_content = f.read()
        if len(sub_content.strip()) < 10:
            self.logger.warning("    Subtitle file empty, skipping")
            self._ffmpeg(["ffmpeg", "-y", "-i", inp, "-c", "copy", out])
            return

        entry_count = sub_content.count("-->")
        self.logger.info(f"    {'ASS' if is_ass else 'SRT'} entries: {entry_count} | "
                         f"FontSize={font_size}, MarginV={margin}")

        # WINDOWS FIX: copy input + subtitle to same temp folder,
        # then run FFmpeg from that folder using relative paths only.
        burn_dir = os.path.join(self.cfg.temp_dir, "burn_subs")
        os.makedirs(burn_dir, exist_ok=True)

        temp_video = os.path.join(burn_dir, "input.mp4")
        temp_sub   = os.path.join(burn_dir, sub_filename)
        temp_out   = os.path.join(burn_dir, "output.mp4")

        shutil.copy2(inp, temp_video)
        shutil.copy2(srt, temp_sub)

        original_dir = os.getcwd()
        os.chdir(burn_dir)

        try:
            if is_ass:
                # ASS: styles (including keyword colours) are baked into the file
                vf = f"subtitles={sub_filename}"
            else:
                # SRT: apply style via force_style
                vf = f"subtitles={sub_filename}:force_style='{style}'"

            cmd = [
                "ffmpeg", "-y",
                "-i", "input.mp4",
                "-vf", vf,
                "-c:v", self.cfg.codec,
                "-c:a", "copy",
                "-pix_fmt", "yuv420p",
                "output.mp4"
            ]
            self.logger.info(f"    Burning subtitles from: {burn_dir}")
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                self.logger.error(f"    Subtitle burn failed: {r.stderr[-500:]}")
                os.chdir(original_dir)
                self._ffmpeg(["ffmpeg", "-y", "-i", inp, "-c", "copy", out])
                return
        finally:
            os.chdir(original_dir)

        shutil.move(temp_out, out)

        try:
            if os.path.exists(temp_video): os.remove(temp_video)
            if os.path.exists(temp_sub):   os.remove(temp_sub)
        except Exception:
            pass

        self.logger.info("    Subtitles burned successfully")

    # ─── BACKGROUND MUSIC ─────────────────────────────────────────

    def _mix_music(self, video, music, out, duration, volume):
        fade_in = self.cfg.bg_music_fade_in
        fade_out = self.cfg.bg_music_fade_out
        fo_start = max(0, duration - fade_out)
        fc = (
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=duration={duration},"
            f"volume={volume},"
            f"afade=t=in:st=0:d={fade_in},"
            f"afade=t=out:st={fo_start:.1f}:d={fade_out}[music];"
            f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        self._ffmpeg([
            "ffmpeg", "-y", "-i", video, "-i", music,
            "-filter_complex", fc,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", self.cfg.audio_codec, "-b:a", self.cfg.audio_bitrate, out
        ])

    # ─── WATERMARK ────────────────────────────────────────────────

    def _add_watermark(self, inp, out):
        wm = self.cfg.watermark_path
        o = self.cfg.watermark_opacity
        pos = {"top-right":"W-w-20:20","top-left":"20:20","bottom-right":"W-w-20:H-h-20","bottom-left":"20:H-h-20"}.get(self.cfg.watermark_position,"W-w-20:20")
        self._ffmpeg([
            "ffmpeg","-y","-i",inp,"-i",wm,
            "-filter_complex",f"[1:v]format=rgba,colorchannelmixer=aa={o}[wm];[0:v][wm]overlay={pos}[vout]",
            "-map","[vout]","-map","0:a","-c:v",self.cfg.codec,"-preset",self.cfg.preset,
            "-crf",str(self.cfg.crf),"-c:a","copy",out
        ])

    # ─── THUMBNAIL ────────────────────────────────────────────────

    def generate_thumbnail(self, background_image, title_text, output_path, vertical=False):
        from PIL import Image, ImageDraw, ImageFont
        tw, th = (1080, 1920) if vertical else (1280, 720)
        fs = 56 if vertical else 72
        max_tw = tw - 100

        img = Image.open(background_image).resize((tw, th), Image.LANCZOS).convert("RGBA")
        grad = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        dg = ImageDraw.Draw(grad)
        for y in range(th):
            dg.rectangle([(0, y), (tw, y+1)], fill=(0, 0, 0, int(180 * (y / th))))
        img = Image.alpha_composite(img, grad)

        draw = ImageDraw.Draw(img)
        try:
            if os.name == "nt":
                font = ImageFont.truetype("C:\\Windows\\Fonts\\arialbd.ttf", fs)
            else:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fs)
        except (IOError, OSError):
            font = ImageFont.load_default()

        words = title_text.split()
        lines, cur = [], []
        for word in words:
            test = " ".join(cur + [word])
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] > max_tw and cur:
                lines.append(" ".join(cur)); cur = [word]
            else:
                cur.append(word)
        if cur: lines.append(" ".join(cur))

        lh = fs + 10
        y_start = th - len(lines) * lh - 80
        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            x = (tw - (bbox[2] - bbox[0])) // 2
            y = y_start + i * lh
            for dx in range(-3, 4):
                for dy in range(-3, 4):
                    draw.text((x+dx, y+dy), line, font=font, fill="black")
            draw.text((x, y), line, font=font, fill="white")

        img.convert("RGB").save(output_path, "JPEG", quality=95)
        return output_path

    # ─── UTILITIES ────────────────────────────────────────────────

    def _get_duration(self, path):
        r = subprocess.run(
            ["ffprobe","-v","quiet","-show_entries","format=duration","-of","csv=p=0",path],
            capture_output=True, text=True)
        try: return float(r.stdout.strip())
        except ValueError: return 0

    def _ffmpeg(self, cmd):
        self.logger.debug(f"  CMD: {' '.join(cmd[:10])}...")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            self.logger.error(f"FFmpeg FAILED (code {r.returncode})")
            self.logger.error(f"STDERR: {r.stderr[-2000:]}")
            raise RuntimeError(f"FFmpeg failed: {r.stderr[-500:]}")

    def cleanup(self):
        if os.path.exists(self.cfg.temp_dir):
            shutil.rmtree(self.cfg.temp_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
#  HELPERS: SRT Generation
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
#  KEYWORD HIGHLIGHTING — for ASS subtitle colour tagging
# ═══════════════════════════════════════════════════════════════

# ASS inline colour format is &HBBGGRR& (no alpha in overrides)
_HIGHLIGHT_COLOR = "&H00FFFF"   # Yellow  (RGB 255,255,0  → BGR 00,FF,FF)
_RESET_COLOR     = "&HFFFFFF"   # White   (RGB 255,255,255 → BGR FF,FF,FF)

# Curated set of high-impact words that always get highlighted
_POWER_WORDS = {
    # scale / money
    "million", "billion", "trillion", "thousand",
    # emphasis
    "never", "always", "every", "only", "first", "last",
    # emotion / hook
    "secret", "truth", "shocking", "warning", "mistake",
    "proven", "finally", "suddenly", "literally", "actually",
    # quality
    "best", "worst", "free", "new", "top", "ultimate",
    "incredible", "amazing", "powerful", "massive", "critical",
    "dangerous", "urgent", "breakthrough", "revolutionary",
    # ai / tech niche
    "ai", "gpt", "llm", "robot", "automation", "algorithm",
    "replace", "future", "data", "model", "chatgpt",
    # world crisis / geopolitics niche
    "war", "nuclear", "nato", "conflict", "crisis", "invasion",
    "attack", "threat", "missile", "bomb", "troops", "military",
    "escalation", "sanction", "alliance", "ceasefire", "casualties",
}

_NUMBER_RE = re.compile(r'^\d[\d,]*(?:\.\d+)?(?:[%xX+])?$')


def _is_keyword(word: str) -> bool:
    """Return True if this word should be highlighted in the subtitle."""
    clean = re.sub(r"[^a-zA-Z0-9%+]", "", word)
    if not clean:
        return False
    if _NUMBER_RE.match(clean):           # numbers, %, x multipliers
        return True
    if clean.lower() in _POWER_WORDS:     # curated impact list
        return True
    if len(clean) >= 8:                   # long words = usually key concepts
        return True
    return False


def _ft_ass(s: float) -> str:
    """Format seconds as ASS timestamp: H:MM:SS.cc (centiseconds)."""
    h  = int(s // 3600)
    m  = int((s % 3600) // 60)
    sc = int(s % 60)
    cs = int((s % 1) * 100)
    return f"{h}:{m:02d}:{sc:02d}.{cs:02d}"


def generate_ass_with_highlights(text, duration, output_ass,
                                  font_size=28, margin_v=60,
                                  width=1920, height=1080,
                                  offset_sec=-0.5):
    """
    Generate an ASS subtitle file where keywords are highlighted in yellow.

    Uses the same sentence-aware chunking + proportional timing as
    generate_srt_from_text, but outputs ASS format so each keyword
    gets an inline colour override tag.

    Keyword rules (see _is_keyword):
      - Numbers / percentages / multipliers
      - Curated high-impact words (_POWER_WORDS)
      - Any word >= 8 characters (usually a key concept)
    """
    MAX_WORDS = 10

    # ── Sentence-aware chunking (same logic as generate_srt_from_text) ──
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    chunks = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) <= MAX_WORDS:
            chunks.append(sentence)
        else:
            parts = re.split(r',\s*', sentence)
            current = []
            for part in parts:
                trial = ((" ".join(current) + " " + part).strip() if current else part)
                if len(trial.split()) <= MAX_WORDS:
                    current.append(part)
                else:
                    if current:
                        chunks.append(", ".join(current))
                    part_words = part.split()
                    for j in range(0, len(part_words), MAX_WORDS):
                        chunks.append(" ".join(part_words[j:j + MAX_WORDS]))
                    current = []
            if current:
                chunks.append(", ".join(current))

    if not chunks:
        return output_ass

    # ── Proportional timing by word count ──
    word_counts = [max(1, len(c.split())) for c in chunks]
    total_words = sum(word_counts)

    # ── Build ASS dialogue lines with inline colour tags ──
    dialogue_lines = []
    t = 0.0
    for chunk, wc in zip(chunks, word_counts):
        chunk_dur = (wc / total_words) * duration
        # Shift timestamps earlier by offset_sec so subtitles stay in sync
        # with speech (voice leads subtitles by ~0.5s without this offset).
        # Clamp to 0 so the first entry never goes negative.
        start = _ft_ass(max(0.0, t + offset_sec))
        end   = _ft_ass(max(0.0, t + chunk_dur + offset_sec))
        t += chunk_dur

        tagged = []
        for word in chunk.split():
            if _is_keyword(word):
                tagged.append(
                    f"{{\\c{_HIGHLIGHT_COLOR}&}}{word}{{\\c{_RESET_COLOR}&}}"
                )
            else:
                tagged.append(word)

        dialogue_lines.append(
            f"Dialogue: 0,{start},{end},Default,,0,0,0,,{' '.join(tagged)}"
        )

    # ── Write ASS file ──
    ass_content = (
        f"[Script Info]\n"
        f"ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        f"ScaledBorderAndShadow: yes\n\n"
        f"[V4+ Styles]\n"
        f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        f"OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        f"ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        f"Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{font_size},"
        f"&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"1,0,0,0,100,100,0,0,1,2,1,5,40,40,0,1\n\n"
        f"[Events]\n"
        f"Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "\n".join(dialogue_lines) + "\n"
    )

    with open(output_ass, "w", encoding="utf-8") as f:
        f.write(ass_content)

    return output_ass


def generate_srt_whisper(audio_path, output_srt, model_size="base"):
    import whisper
    logger = logging.getLogger("WhisperSRT")
    logger.info(f"Transcribing with Whisper ({model_size})...")
    model = whisper.load_model(model_size)
    result = model.transcribe(audio_path, language="en", word_timestamps=True, verbose=False)
    lines, counter = [], 1
    for seg in result["segments"]:
        words = seg.get("words", [])
        if not words:
            lines += [str(counter), f"{_ft(seg['start'])} --> {_ft(seg['end'])}", seg["text"].strip(), ""]
            counter += 1; continue
        chunk, cs = [], None
        for w in words:
            if cs is None: cs = w["start"]
            chunk.append(w["word"].strip())
            if len(chunk) >= 8 or w == words[-1]:
                lines += [str(counter), f"{_ft(cs)} --> {_ft(w['end'])}", " ".join(chunk), ""]
                counter += 1; chunk, cs = [], None
    with open(output_srt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"SRT: {counter-1} entries → {output_srt}")
    return output_srt


def generate_srt_from_text(text, duration, output_srt):
    """
    Generate SRT from plain text + total audio duration.

    Strategy:
      1. Split on sentence boundaries (. ! ?) so captions never straddle sentences.
      2. Break sentences longer than MAX_WORDS into sub-chunks at natural pauses
         (commas first, then hard word-count split).
      3. Distribute display time proportionally by word count so longer
         phrases stay on screen longer — reducing the drift you see with
         equal-time chunking.
    """
    MAX_WORDS = 10  # max words per subtitle line

    # ── Step 1: sentence-aware split ──────────────────────────────
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    # ── Step 2: chunk each sentence ───────────────────────────────
    chunks = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) <= MAX_WORDS:
            chunks.append(sentence)
        else:
            # Try to break at commas first for a more natural pause
            parts = re.split(r',\s*', sentence)
            current = []
            for part in parts:
                trial = ((" ".join(current) + " " + part).strip() if current else part)
                if len(trial.split()) <= MAX_WORDS:
                    current.append(part)
                else:
                    if current:
                        chunks.append(", ".join(current))
                    # If this single part is still too long, hard-split it
                    part_words = part.split()
                    for j in range(0, len(part_words), MAX_WORDS):
                        chunks.append(" ".join(part_words[j:j + MAX_WORDS]))
                    current = []
            if current:
                chunks.append(", ".join(current))

    if not chunks:
        return output_srt

    # ── Step 3: proportional timing by word count ─────────────────
    word_counts = [max(1, len(c.split())) for c in chunks]
    total_words = sum(word_counts)
    lines = []
    t = 0.0
    for i, (chunk, wc) in enumerate(zip(chunks, word_counts)):
        chunk_dur = (wc / total_words) * duration
        s, e = t, t + chunk_dur
        lines += [str(i + 1), f"{_ft(s)} --> {_ft(e)}", chunk, ""]
        t = e

    with open(output_srt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return output_srt


def _ft(s):
    h, m, sc, ms = int(s//3600), int((s%3600)//60), int(s%60), int((s%1)*1000)
    return f"{h:02d}:{m:02d}:{sc:02d},{ms:03d}"


# ═══════════════════════════════════════════════════════════════
#  HELPERS: Image Prompts + Pexels Fetch
# ═══════════════════════════════════════════════════════════════

def generate_image_prompts(script_text, num_images=20, orientation="landscape", provider="anthropic"):
    logger = logging.getLogger("ImagePrompts")
    orient_note = "VERTICAL 9:16 portrait" if orientation == "portrait" else "HORIZONTAL 16:9 landscape"
    prompt = f"""Generate exactly {num_images} image descriptions for a slideshow video.
SCRIPT: {script_text[:3000]}
RULES:
- Each: 1-2 sentences for Pexels stock photo search
- Progress with the script chronologically
- Each visually DISTINCT from the previous
- All must work in {orient_note} format
- Return ONLY a numbered list, no other text"""

    if provider == "anthropic":
        import anthropic
        raw = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY")).messages.create(
            model="claude-sonnet-4-20250514", max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]).content[0].text
    elif provider == "openai":
        import openai
        raw = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY")).chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}],
            max_tokens=2000).choices[0].message.content
    else:
        words = script_text.split()
        cs = max(1, len(words) // num_images)
        return [f"professional photo of {' '.join(words[i*cs:(i*cs)+5])}" for i in range(num_images)]

    prompts = [re.sub(r'^\d+[\.\)\:\-]\s*', '', line.strip()) for line in raw.strip().split("\n") if line.strip()]
    while len(prompts) < num_images:
        prompts.append("abstract technology background, futuristic, blue tones")
    return prompts[:num_images]


def fetch_pexels_images(prompts, output_dir, orientation="landscape"):
    import requests
    logger = logging.getLogger("PexelsFetch")
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key: raise ValueError("PEXELS_API_KEY not set")
    os.makedirs(output_dir, exist_ok=True)
    paths = []

    for i, query in enumerate(prompts):
        path = os.path.join(output_dir, f"img_{i:03d}.jpg")
        search_q = " ".join(query.split()[:6])
        try:
            resp = requests.get("https://api.pexels.com/v1/search",
                headers={"Authorization": api_key},
                params={"query": search_q, "per_page": 5, "orientation": orientation}, timeout=10)
            resp.raise_for_status()
            photos = resp.json().get("photos", [])
            if not photos:
                resp = requests.get("https://api.pexels.com/v1/search",
                    headers={"Authorization": api_key},
                    params={"query": search_q.split()[0], "per_page": 5, "orientation": orientation}, timeout=10)
                resp.raise_for_status()
                photos = resp.json().get("photos", [])
            if photos:
                photo = random.choice(photos)
                img_url = photo["src"].get("large2x") or photo["src"]["large"]
                img_data = requests.get(img_url, timeout=30).content
                with open(path, "wb") as f: f.write(img_data)
                logger.info(f"  [{i+1}/{len(prompts)}] {search_q[:40]}")
            else:
                _placeholder(path, query[:30], orientation)
        except Exception as e:
            logger.warning(f"  [{i+1}] Failed: {e}")
            _placeholder(path, query[:30], orientation)
        paths.append(path)
    return paths


def _placeholder(path, text, orientation):
    from PIL import Image, ImageDraw, ImageFont
    w, h = (1920, 1080) if orientation == "landscape" else (1080, 1920)
    img = Image.new("RGB", (w, h), (15, 15, 35))
    draw = ImageDraw.Draw(img)
    try:
        if os.name == "nt": font = ImageFont.truetype("C:\\Windows\\Fonts\\arialbd.ttf", 48)
        else: font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
    except: font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(((w-(bbox[2]-bbox[0]))//2, h//2), text, fill="white", font=font)
    img.save(path)


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    print("""
    SLIDESHOW ASSEMBLER v4 — Long-Form + Reels
    ============================================
    Long-form:  assembler.assemble(audio, images, out)
    Reel/Short: assembler.assemble_reel(audio, images, out)

    Subtitle fix: music first, then subs (Windows path safe)
    Font: 42px Reels, 28px Long-form
    """)
