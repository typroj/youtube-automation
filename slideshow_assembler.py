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
    reel_max_duration: int = 89  # Instagram supports up to 90s Reels

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

    # Vintage film effect (old-footage look for heritage/history niches)
    vintage_effect: bool = False

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
                 srt_path=None, bg_music_path=None, hook_text=""):
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
            hook_text=hook_text,
        )

    # ─── REELS / SHORTS ──────────────────────────────────────────

    def assemble_reel(self, audio_path, image_paths, output_path,
                      srt_path=None, bg_music_path=None, hook_text=""):
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
            hook_text=hook_text,
        )

    # ─── SHARED RENDERING ENGINE ──────────────────────────────────

    def _render_video(self, audio_path, image_paths, output_path, srt_path,
                      bg_music_path, width, height, min_sec, max_sec,
                      effects_list, sub_font_size, sub_margin, music_vol,
                      max_duration, hook_text=""):

        # Step 1: Audio duration — speed up instead of cutting if over limit
        audio_duration = self._get_duration(audio_path)
        if max_duration and audio_duration > max_duration + 1.0:  # 1s tolerance avoids float no-ops
            speed = audio_duration / max_duration
            sped_audio = os.path.join(self.cfg.temp_dir, "audio_tempo_adj.mp3")  # distinct name avoids in-place collision
            self.logger.info(f"  Audio {audio_duration:.1f}s > {max_duration}s limit — speeding up {speed:.2f}x")
            self._speed_audio(audio_path, sped_audio, speed)
            audio_path = sped_audio
            audio_duration = max_duration
        self.logger.info(f"  Audio: {audio_duration:.1f}s | Res: {width}x{height}")

        # Step 2: Image durations
        durations = self._calc_durations(len(image_paths), audio_duration, min_sec, max_sec)
        self.logger.info(f"  Images: {len(durations)}, avg {sum(durations)/len(durations):.1f}s each")

        # Step 3: Assign effects
        effects = self._assign_effects(len(image_paths), effects_list)

        # Step 4: Render clips (images or video clips)
        _video_exts = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
        n_vids = sum(1 for p in image_paths if Path(p).suffix.lower() in _video_exts)
        label = "video clips" if n_vids == len(image_paths) else f"media ({n_vids} videos, {len(image_paths)-n_vids} images)"
        self.logger.info(f"  Rendering {label}...")
        clip_paths = []
        for i, (img, dur, eff) in enumerate(zip(image_paths, durations, effects)):
            clip = os.path.join(self.cfg.temp_dir, f"clip_{i:03d}.mp4")
            if Path(img).suffix.lower() in _video_exts:
                self._render_video_clip(img, clip, dur, width, height)
            else:
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

        # Step 8.5: Engagement overlays (progress bar + hook card + subscribe CTA)
        self.logger.info("  Adding engagement overlays...")
        engaged_out = os.path.join(self.cfg.temp_dir, "engaged.mp4")
        self._add_engagement_overlays(current, engaged_out, audio_duration,
                                      hook_text, width, height)
        current = engaged_out

        # Step 9: Safety trim (only fires if engagement overlays added unexpected length)
        if max_duration:
            actual_dur = self._get_duration(current)
            if actual_dur > max_duration + 2:  # 2s tolerance for overlay rounding
                self.logger.info(f"  Safety trim {actual_dur:.0f}s → {max_duration}s...")
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

    # ─── RENDER SINGLE CLIP (image or video) ─────────────────────

    def _vintage_vf(self) -> str:
        """
        FFmpeg filter chain that makes footage look like old film / ancient documentary.

        Layers applied (in order):
          1. eq          — lower brightness, lower saturation, slight contrast boost
          2. colorbalance — warm sepia tint (more red/yellow, less blue)
          3. noise        — film grain (temporal+uniform)
          4. vignette     — dark edges, like an old projector lens
        """
        return (
            "eq=contrast=1.12:brightness=-0.06:saturation=0.38:gamma=1.06,"
            "colorbalance=rs=0.12:gs=0.02:bs=-0.18:rm=0.07:gm=0.01:bm=-0.10,"
            "noise=alls=22:allf=t+u,"
            "vignette=angle=PI/4:mode=forward"
        )

    def _render_video_clip(self, video_path, out, dur, w, h):
        """
        Trim + scale a Pexels video clip to the required duration and resolution.
        Strips audio (audio is mixed in later).
        Applies vintage film effect if cfg.vintage_effect is True.
        """
        base_vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}:(iw-{w})/2:(ih-{h})/2,"
            f"setsar=1"
        )
        vf = f"{base_vf},{self._vintage_vf()}" if self.cfg.vintage_effect else base_vf
        self._ffmpeg([
            "ffmpeg", "-y", "-i", video_path,
            "-vf", vf,
            "-t", str(dur),
            "-r", str(self.cfg.fps),
            "-c:v", self.cfg.codec, "-preset", "fast",
            "-crf", str(self.cfg.crf + 2),
            "-an", "-pix_fmt", "yuv420p", out,
        ])

    def _render_clip(self, img, out, dur, effect, w, h):
        fps = self.cfg.fps
        frames = int(dur * fps) + fps
        vf = self._effect_filter(effect, w, h, dur + 1, fps, frames)
        if self.cfg.vintage_effect:
            vf = f"{vf},{self._vintage_vf()}"
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

        entry_count = sub_content.count("Dialogue:") if is_ass else sub_content.count("-->")
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

    # ─── AUDIO SPEED ──────────────────────────────────────────────

    def _speed_audio(self, inp, out, speed):
        """Speed up audio with atempo. Chains filters when speed > 2.0."""
        chain = []
        remaining = speed
        while remaining > 2.0:
            chain.append("atempo=2.0")
            remaining /= 2.0
        chain.append(f"atempo={remaining:.4f}")
        self._ffmpeg([
            "ffmpeg", "-y", "-i", inp,
            "-filter:a", ",".join(chain),
            "-c:a", "libmp3lame", "-q:a", "2", out,
        ])

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

    # ─── ENGAGEMENT OVERLAYS ──────────────────────────────────────

    def _add_engagement_overlays(self, inp, out, duration, hook_text, width, height):
        """
        Burns engagement layers into the video via FFmpeg:

          1. Hook banner   — full-width dark band at the bottom-third with
                             two-line reveal (line 1 at 0s, line 2 at 1.2s).
                             Positioned where thumbs hover while scrolling IG.

          3. Progress bar  — amber bar at the very bottom edge.

          4. Subscribe CTA — appears during last 3.5 s, bottom area.
        """
        is_vertical = height > width

        # Font path — colon after drive letter must be escaped as \: for FFmpeg
        if os.name == "nt":
            font = "C\\:/Windows/Fonts/arialbd.ttf"
        else:
            font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

        hook_fs = 76 if is_vertical else 54
        cta_fs  = 44 if is_vertical else 32
        bar_h   = 12 if is_vertical else 8

        vf_parts = []

        # ── 2. Hook banner (bottom-third, two-line reveal) ────────
        if hook_text and hook_text.strip():
            safe = re.sub(r"[':,\\\[\]{}]", " ", hook_text).strip()

            # Split into two lines at midpoint word boundary
            words = safe.split()
            if len(words) > 3:
                mid = len(words) // 2
                line1 = " ".join(words[:mid])
                line2 = " ".join(words[mid:])
            else:
                line1 = safe
                line2 = ""

            # Truncate each line so it fits within frame
            max_chars = 22 if is_vertical else 35
            line1 = line1[:max_chars]
            line2 = line2[:max_chars]

            # Full-width semi-transparent band behind the text
            band_y  = "ih*0.58" if is_vertical else "ih*0.62"
            band_h  = int(height * (0.22 if is_vertical else 0.20))
            hook_band = (
                f"drawbox=x=0:y={band_y}:w=iw:h={band_h}:"
                f"color=black@0.82:t=fill:"
                f"enable='between(t,0,3.0)'"
            )
            vf_parts.append(hook_band)

            # Line 1 — appears immediately
            text_y1 = f"H*{'0.61' if is_vertical else '0.65'}"
            hook_line1 = (
                f"drawtext=fontfile='{font}':"
                f"text='{line1}':"
                f"fontsize={hook_fs}:fontcolor=white:"
                f"x=(W-tw)/2:y={text_y1}:"
                f"enable='between(t,0,3.0)'"
            )
            vf_parts.append(hook_line1)

            # Line 2 — appears at 1.2 s (staggered reveal)
            if line2:
                text_y2 = f"H*{'0.70' if is_vertical else '0.73'}"
                hook_line2 = (
                    f"drawtext=fontfile='{font}':"
                    f"text='{line2}':"
                    f"fontsize={hook_fs}:fontcolor=#FFD700:"
                    f"x=(W-tw)/2:y={text_y2}:"
                    f"enable='between(t,1.2,3.0)'"
                )
                vf_parts.append(hook_line2)

        # ── 3. Progress bar (bottom edge) ─────────────────────────
        progress = (
            f"drawbox=x=0:y=ih-{bar_h}:"
            f"w='(t/{duration:.3f})*iw':"
            f"h={bar_h}:color=#FFB300@1:t=fill"
        )
        vf_parts.append(progress)

        # ── 4. Subscribe CTA (bottom area, last 3.5 s) ────────────
        cta_start = max(1.0, duration - 3.5)
        cta = (
            f"drawtext=fontfile='{font}':"
            f"text='LIKE and SUBSCRIBE for more':"
            f"fontsize={cta_fs}:fontcolor=yellow:"
            f"x=(W-tw)/2:y=H*0.88:"
            f"box=1:boxcolor=black@0.60:boxborderw=16:"
            f"enable='gte(t,{cta_start:.2f})'"
        )
        vf_parts.append(cta)

        self._ffmpeg([
            "ffmpeg", "-y", "-i", inp,
            "-vf", ",".join(vf_parts),
            "-c:v", self.cfg.codec, "-preset", self.cfg.preset,
            "-crf", str(self.cfg.crf),
            "-c:a", "copy", "-pix_fmt", "yuv420p", out,
        ])
        self.logger.info("  Engagement overlays: hook banner + progress bar + CTA")

    # ─── WATERMARK ───────────────────────────────────────────────��

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
    # tech gadgets niche
    "smartphone", "foldable", "holographic", "drone", "camera",
    "smartwatch", "earbuds", "playstation", "xbox", "wearable",
    "spatial", "exoskeleton", "hologram", "biometric", "haptic",
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
                                  offset_sec=-0.5,
                                  hook_duration=2.5):
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

        # Apply sync offset (shift earlier so subtitles match speech)
        raw_start = max(0.0, t + offset_sec)
        raw_end   = max(0.0, t + chunk_dur + offset_sec)
        t += chunk_dur

        # Keep subtitles hidden while the hook title card is on screen.
        # Both the hook card and subtitles occupy centre-screen, so showing
        # them simultaneously causes subtitles to be obscured by the card.
        if raw_end <= hook_duration:
            continue                              # entire chunk inside hook window → skip
        if raw_start < hook_duration:
            raw_start = hook_duration             # partial overlap → trim start to after hook

        start = _ft_ass(raw_start)
        end   = _ft_ass(raw_end)

        dialogue_lines.append(
            f"Dialogue: 0,{start},{end},Default,,0,0,0,,{_tag_keywords(chunk)}"
        )

    with open(output_ass, "w", encoding="utf-8") as f:
        f.write(_ass_file(dialogue_lines, font_size, margin_v, width, height))

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


def _tag_keywords(text: str) -> str:
    """Wrap keywords in ASS yellow colour override tags."""
    tagged = []
    for word in text.split():
        if _is_keyword(word):
            tagged.append(f"{{\\c{_HIGHLIGHT_COLOR}&}}{word}{{\\c{_RESET_COLOR}&}}")
        else:
            tagged.append(word)
    return " ".join(tagged)


def _ass_file(dialogue_lines, font_size, margin_v, width, height):
    """Build and return a complete ASS file string from dialogue lines."""
    return (
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


def _transcribe_words(audio_path, model_size):
    """
    Transcribe audio and return a flat list of word dicts {word, start, end}.

    Tries faster-whisper first (CTranslate2, no PyTorch).
    Falls back to openai-whisper.
    Returns None if both fail (caller should fall back to text-based ASS).

    Install (recommended):  pip install faster-whisper
    Fallback:               pip install openai-whisper
    """
    logger = logging.getLogger("WhisperASS")

    # ── faster-whisper (preferred — CTranslate2, no PyTorch DLL) ──────────
    # Try float32 first (universally compatible), then int8 (needs AVX2).
    for compute_type in ("float32", "int8"):
        try:
            from faster_whisper import WhisperModel
            logger.info(f"  Using faster-whisper (compute_type={compute_type})…")
            model    = WhisperModel(model_size, device="cpu", compute_type=compute_type)
            segments, _ = model.transcribe(audio_path, word_timestamps=True,
                                            language="en")
            words = []
            for seg in segments:
                for w in (seg.words or []):
                    words.append({"word": w.word.strip(),
                                   "start": w.start, "end": w.end})
            logger.info(f"  faster-whisper ({compute_type}): {len(words)} words transcribed")
            return words

        except Exception as e:
            logger.warning(f"  faster-whisper compute_type={compute_type} failed ({type(e).__name__}: {e})")

    logger.warning("  All faster-whisper compute types failed — trying openai-whisper…")

    # ── openai-whisper fallback ────────────────────────────────────────────
    try:
        import whisper
        logger.info("  Using openai-whisper…")
        model  = whisper.load_model(model_size)
        result = model.transcribe(audio_path, language="en",
                                   word_timestamps=True, verbose=False)
        words = []
        for seg in result["segments"]:
            for w in seg.get("words", []):
                words.append({"word": w["word"].strip(),
                               "start": w["start"], "end": w["end"]})
        logger.info(f"  openai-whisper: {len(words)} words transcribed")
        return words

    except Exception as e:
        logger.error(f"  openai-whisper also failed ({type(e).__name__}: {e})")

    # ── Both failed ────────────────────────────────────────────────────────
    logger.error(
        "\n  ✗ No Whisper backend available — karaoke subtitles disabled.\n"
        "  On Windows, DLL errors are usually fixed by:\n"
        "    1) pip install faster-whisper\n"
        "    2) Install Visual C++ Redistributable 2015-2022 from Microsoft\n"
        "       (search: 'vc_redist.x64.exe')\n"
        "  Falling back to text-based ASS subtitles.\n"
    )
    return None


def generate_ass_whisper_with_highlights(audio_path, output_ass,
                                          font_size=28, margin_v=60,
                                          width=1920, height=1080,
                                          hook_duration=2.5,
                                          model_size="small",
                                          narration_text=None,
                                          audio_duration=0.0):
    """
    Karaoke-style ASS subtitles using Whisper word-level timestamps.

    Each word changes colour in real-time as speech progresses:
      Gray   — already spoken
      Yellow — currently speaking  (active word)
      White  — coming up next  (Yellow if it is a keyword)

    If both faster-whisper and openai-whisper fail (e.g. DLL errors on
    Windows), automatically falls back to text-based ASS when narration_text
    and audio_duration are provided.

    Recommended install:  pip install faster-whisper
    """
    # ── Karaoke colour palette (ASS BGR format) ──────────────────
    SPOKEN      = "&HC0C0C0"   # light grey — word is done
    ACTIVE      = "&H00A5FF"   # dark yellow (orange-gold) — word being spoken right now
    UPCOMING    = "&HFFFFFF"   # white  — not spoken yet
    KW_UPCOMING = "&H00A5FF"   # dark yellow (orange-gold) — upcoming keyword

    CHUNK_SIZE = 7

    logger = logging.getLogger("WhisperASS")
    logger.info(f"  Transcribing with faster-whisper / whisper ({model_size})…")

    all_words = _transcribe_words(audio_path, model_size)

    # ── Graceful fallback when Whisper is unavailable ─────────────
    if all_words is None:
        if narration_text and audio_duration > 0:
            logger.warning("  Whisper unavailable — using text-based ASS fallback")
            return generate_ass_with_highlights(
                narration_text, audio_duration, output_ass,
                font_size=font_size, margin_v=margin_v,
                width=width, height=height, hook_duration=hook_duration,
            )
        logger.error("  Whisper unavailable and no narration_text provided — writing empty ASS")
        all_words = []

    dialogue_lines = []
    total          = len(all_words)

    if not all_words:
        logger.warning("  No word timestamps returned — subtitles will be empty")
    else:
        for i, w in enumerate(all_words):
            frame_start = w["start"]
            frame_end   = all_words[i + 1]["start"] if i + 1 < total else w["end"]

            if frame_end <= hook_duration:
                continue
            frame_start = max(frame_start, hook_duration)

            chunk_idx   = (i // CHUNK_SIZE) * CHUNK_SIZE
            chunk_words = all_words[chunk_idx : chunk_idx + CHUNK_SIZE]

            parts, cur_color = [], None
            for j, cw in enumerate(chunk_words):
                abs_j = chunk_idx + j
                if abs_j < i:
                    color = SPOKEN
                elif abs_j == i:
                    color = ACTIVE
                else:
                    color = KW_UPCOMING if _is_keyword(cw["word"]) else UPCOMING

                if abs_j == i:
                    # Active word: yellow + bold + 120% scale, then reset for next word
                    parts.append(
                        f"{{\\c{ACTIVE}&\\b1\\fscx120\\fscy120}}{cw['word']}"
                        f"{{\\b0\\fscx100\\fscy100}}"
                    )
                    cur_color = ACTIVE
                elif color != cur_color:
                    parts.append(f"{{\\c{color}&}}{cw['word']}")
                    cur_color = color
                else:
                    parts.append(cw["word"])

            dialogue_lines.append(
                f"Dialogue: 0,{_ft_ass(frame_start)},{_ft_ass(frame_end)},"
                f"Default,,0,0,0,,{' '.join(parts)}"
            )

    with open(output_ass, "w", encoding="utf-8") as f:
        f.write(_ass_file(dialogue_lines, font_size, margin_v, width, height))

    logger.info(f"  Karaoke ASS: {len(dialogue_lines)} entries → {output_ass}")
    return output_ass


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
# ═══════════════════════════════════════════════════════��═══════

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


def fetch_pexels_videos(prompts, output_dir, orientation="landscape", search_modifier=""):
    """
    Download short Pexels video clips for each prompt.
    Returns list of local .mp4 paths (same length as prompts).
    Falls back to a black placeholder clip if nothing is found.

    search_modifier: optional prefix appended to every query to steer results
      e.g. "ancient historical" for heritage niche
    """
    import requests
    logger = logging.getLogger("PexelsFetch")
    api_key = os.environ.get("PEXELS_API_KEY")
    if not api_key:
        raise ValueError("PEXELS_API_KEY not set")
    os.makedirs(output_dir, exist_ok=True)
    paths = []

    # Target resolution for file selection
    want_portrait = orientation == "portrait"

    for i, query in enumerate(prompts):
        out_path = os.path.join(output_dir, f"clip_{i:03d}.mp4")
        # Use first 6 words of the prompt; prepend niche modifier for relevance
        core_q   = " ".join(query.split()[:6])
        search_q = f"{search_modifier} {core_q}".strip() if search_modifier else core_q
        try:
            resp = requests.get(
                "https://api.pexels.com/videos/search",
                headers={"Authorization": api_key},
                params={"query": search_q, "per_page": 8,
                        "orientation": orientation},
                timeout=15,
            )
            resp.raise_for_status()
            videos = resp.json().get("videos", [])

            # Fallback 1: drop the modifier, use core query only
            if not videos and search_modifier:
                resp = requests.get(
                    "https://api.pexels.com/videos/search",
                    headers={"Authorization": api_key},
                    params={"query": core_q, "per_page": 8,
                            "orientation": orientation},
                    timeout=15,
                )
                resp.raise_for_status()
                videos = resp.json().get("videos", [])

            # Fallback 2: broaden to first keyword only
            if not videos:
                resp = requests.get(
                    "https://api.pexels.com/videos/search",
                    headers={"Authorization": api_key},
                    params={"query": core_q.split()[0], "per_page": 8,
                            "orientation": orientation},
                    timeout=15,
                )
                resp.raise_for_status()
                videos = resp.json().get("videos", [])

            if videos:
                video = random.choice(videos)
                files = video.get("video_files", [])

                # Pick best file: prefer HD at target orientation
                def _score(f):
                    h, w = f.get("height", 0), f.get("width", 0)
                    orientation_ok = (h > w) if want_portrait else (w >= h)
                    quality_score = min(h, 1080)
                    return (int(orientation_ok) * 10000) + quality_score

                best = max(files, key=_score) if files else None
                if best:
                    vid_data = requests.get(best["link"], stream=True, timeout=60)
                    with open(out_path, "wb") as f:
                        for chunk in vid_data.iter_content(chunk_size=65536):
                            f.write(chunk)
                    logger.info(f"  [{i+1}/{len(prompts)}] {search_q[:40]}"
                                f" ({best.get('height')}p)")
                    paths.append(out_path)
                    continue

        except Exception as e:
            logger.warning(f"  [{i+1}] Video fetch failed: {e}")

        # Fallback: generate a black placeholder clip (1s, will be looped by FFmpeg)
        _placeholder_video(out_path, query[:30], orientation)
        paths.append(out_path)

    return paths


def _placeholder_video(path, text, orientation):
    """Generate a short black MP4 clip as fallback when Pexels returns nothing."""
    w, h = (1080, 1920) if orientation == "portrait" else (1920, 1080)
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=30:d=5",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", "5", "-c:v", "libx264", "-c:a", "aac",
        "-pix_fmt", "yuv420p", path,
    ], capture_output=True)


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
