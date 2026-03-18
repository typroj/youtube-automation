"""
VIDEO ASSEMBLER — Faceless YouTube Automation Pipeline
=======================================================
Production-ready module that composites AI-generated visuals, TTS audio,
burned subtitles, background music, and transitions into a final YouTube-ready video.

Pipeline: scenes[] → image effects → audio sync → subtitle burn → bg music → render

Dependencies:
    pip install moviepy==2.1.2 Pillow numpy pysrt whisper-openai ffmpeg-python

System Requirements:
    - FFmpeg (apt install ffmpeg)
    - ImageMagick (apt install imagemagick) — for TextClip rendering
    - 2+ vCPU, 8GB RAM recommended for 1080p rendering
"""

import os
import json
import logging
import subprocess
import tempfile
import random
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ─── Configuration ────────────────────────────────────────────────────────────

@dataclass
class VideoConfig:
    """Master configuration for video assembly."""

    # Output dimensions
    width: int = 1920
    height: int = 1080
    fps: int = 30

    # Encoding
    codec: str = "libx264"
    preset: str = "medium"       # ultrafast/fast/medium/slow (speed vs quality)
    crf: int = 20                # 18=high quality, 23=default, 28=low
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"

    # Visual effects
    default_image_effect: str = "kenburns"  # kenburns / zoom_in / zoom_out / pan_left / pan_right / static
    crossfade_duration: float = 0.5         # seconds between scenes
    
    # Subtitles
    subtitle_font: str = "Arial-Bold"
    subtitle_font_size: int = 52
    subtitle_color: str = "white"
    subtitle_bg_color: str = "black@0.6"  # semi-transparent background
    subtitle_position: str = "bottom"      # bottom / center
    subtitle_margin_bottom: int = 60
    max_chars_per_line: int = 40

    # Background music
    bg_music_volume: float = 0.08   # 0.0–1.0 (keep very low, 0.05–0.15)
    
    # Intro/Outro
    intro_duration: float = 0.0     # set >0 if you have an intro clip
    outro_duration: float = 0.0
    
    # Branding
    watermark_path: Optional[str] = None
    watermark_opacity: float = 0.3
    watermark_position: str = "top-right"  # top-right / top-left / bottom-right / bottom-left

    # Paths
    temp_dir: str = "/tmp/video_assembler"
    output_dir: str = "./output"
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


@dataclass
class Scene:
    """A single scene in the video — one image/video + corresponding audio segment."""
    scene_id: int
    image_path: str                     # Path to visual asset (image or video clip)
    audio_path: str                     # Path to TTS audio segment
    text: str = ""                      # Script text for this scene (used in subtitles)
    duration: Optional[float] = None    # Override duration (else uses audio length)
    effect: Optional[str] = None        # Override default effect for this scene
    
    # Populated during processing
    audio_duration: float = 0.0
    subtitle_entries: List[Dict] = field(default_factory=list)


# ─── Core Video Assembler ─────────────────────────────────────────────────────

class VideoAssembler:
    """
    Assembles faceless YouTube videos from scenes (images + audio + subtitles).
    
    Usage:
        config = VideoConfig(width=1920, height=1080)
        assembler = VideoAssembler(config)
        
        scenes = [
            Scene(scene_id=0, image_path="img_0.png", audio_path="audio_0.mp3", text="..."),
            Scene(scene_id=1, image_path="img_1.png", audio_path="audio_1.mp3", text="..."),
        ]
        
        output = assembler.assemble(
            scenes=scenes,
            output_path="final_video.mp4",
            bg_music_path="lofi_bg.mp3",     # optional
            subtitle_srt_path="subs.srt",     # optional (auto-generated if not provided)
        )
    """

    def __init__(self, config: VideoConfig = None):
        self.config = config or VideoConfig()
        self.logger = logging.getLogger("VideoAssembler")
        self._setup_dirs()

    def _setup_dirs(self):
        """Create working directories."""
        os.makedirs(self.config.temp_dir, exist_ok=True)
        os.makedirs(self.config.output_dir, exist_ok=True)

    # ─── PUBLIC API ───────────────────────────────────────────────────────

    def assemble(
        self,
        scenes: List[Scene],
        output_path: str,
        bg_music_path: Optional[str] = None,
        subtitle_srt_path: Optional[str] = None,
        intro_clip_path: Optional[str] = None,
        outro_clip_path: Optional[str] = None,
    ) -> str:
        """
        Full assembly pipeline. Returns path to the rendered video.
        
        Steps:
            1. Probe audio durations for each scene
            2. Generate subtitle SRT (if not provided)
            3. Render each scene (image + effect + audio)
            4. Build FFmpeg concat manifest
            5. Concatenate all scenes with crossfade
            6. Burn subtitles
            7. Mix background music
            8. Add watermark (if configured)
            9. Add intro/outro (if provided)
            10. Final render
        """
        self.logger.info(f"Starting assembly: {len(scenes)} scenes → {output_path}")
        
        # Step 1: Probe audio durations
        for scene in scenes:
            scene.audio_duration = self._get_audio_duration(scene.audio_path)
            if scene.duration is None:
                scene.duration = scene.audio_duration
            self.logger.info(f"  Scene {scene.scene_id}: {scene.duration:.2f}s")

        total_duration = sum(s.duration for s in scenes)
        self.logger.info(f"  Total duration: {total_duration:.1f}s ({total_duration/60:.1f} min)")
        
        # Step 2: Generate SRT if needed
        if subtitle_srt_path is None:
            subtitle_srt_path = self._generate_srt_from_scenes(scenes)
        
        # Step 3: Render individual scene clips
        scene_clips = []
        for scene in scenes:
            clip_path = self._render_scene(scene)
            scene_clips.append(clip_path)
        
        # Step 4–5: Concatenate scenes
        concat_path = os.path.join(self.config.temp_dir, "concat_raw.mp4")
        self._concatenate_scenes(scene_clips, concat_path)
        
        # Step 6: Burn subtitles
        subtitled_path = os.path.join(self.config.temp_dir, "subtitled.mp4")
        self._burn_subtitles(concat_path, subtitle_srt_path, subtitled_path)
        
        # Step 7: Mix background music
        current_path = subtitled_path
        if bg_music_path and os.path.exists(bg_music_path):
            music_path = os.path.join(self.config.temp_dir, "with_music.mp4")
            self._mix_background_music(current_path, bg_music_path, music_path, total_duration)
            current_path = music_path
        
        # Step 8: Add watermark
        if self.config.watermark_path and os.path.exists(self.config.watermark_path):
            wm_path = os.path.join(self.config.temp_dir, "watermarked.mp4")
            self._add_watermark(current_path, wm_path)
            current_path = wm_path
        
        # Step 9: Add intro/outro
        if intro_clip_path or outro_clip_path:
            final_parts = []
            if intro_clip_path and os.path.exists(intro_clip_path):
                final_parts.append(intro_clip_path)
            final_parts.append(current_path)
            if outro_clip_path and os.path.exists(outro_clip_path):
                final_parts.append(outro_clip_path)
            
            if len(final_parts) > 1:
                with_intro_path = os.path.join(self.config.temp_dir, "with_intro_outro.mp4")
                self._concatenate_scenes(final_parts, with_intro_path)
                current_path = with_intro_path
        
        # Step 10: Final copy to output
        final_output = os.path.join(self.config.output_dir, os.path.basename(output_path))
        self._ffmpeg_run([
            "ffmpeg", "-y", "-i", current_path,
            "-c", "copy", final_output
        ])
        
        file_size_mb = os.path.getsize(final_output) / (1024 * 1024)
        self.logger.info(f"✅ Assembly complete: {final_output} ({file_size_mb:.1f} MB)")
        return final_output

    # ─── SCENE RENDERING ──────────────────────────────────────────────────

    def _render_scene(self, scene: Scene) -> str:
        """
        Render a single scene: apply image effect (Ken Burns/zoom/pan) 
        and sync with audio. Returns path to scene clip.
        """
        effect = scene.effect or self.config.default_image_effect
        output = os.path.join(self.config.temp_dir, f"scene_{scene.scene_id:03d}.mp4")
        
        w, h = self.config.width, self.config.height
        duration = scene.duration
        fps = self.config.fps
        
        # Build the video filter for the chosen effect
        vf = self._build_effect_filter(effect, w, h, duration, fps)
        
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", scene.image_path,
            "-i", scene.audio_path,
            "-vf", vf,
            "-c:v", self.config.codec,
            "-preset", self.config.preset,
            "-crf", str(self.config.crf),
            "-c:a", self.config.audio_codec,
            "-b:a", self.config.audio_bitrate,
            "-shortest",
            "-pix_fmt", "yuv420p",
            "-t", str(duration),
            output
        ]
        
        self._ffmpeg_run(cmd)
        self.logger.info(f"  Rendered scene {scene.scene_id} ({effect}, {duration:.1f}s)")
        return output

    def _build_effect_filter(self, effect: str, w: int, h: int, duration: float, fps: int) -> str:
        """
        Build FFmpeg video filter string for image animation effects.
        
        All effects work by scaling the image larger than the output frame, 
        then animating a crop window across it over time.
        
        The key formula: 
            zoompan=z='zoom_expr':x='x_expr':y='y_expr':d=total_frames:s=WxH:fps=FPS
        """
        total_frames = int(duration * fps)
        
        effects = {
            # Ken Burns: slow zoom in + subtle pan (most cinematic)
            "kenburns": (
                f"scale=8000:-1,"
                f"zoompan=z='min(zoom+0.0015,1.5)':"
                f"x='if(gte(zoom,1.5),x,x+1)':"
                f"y='if(gte(zoom,1.5),y,y+0.5)':"
                f"d={total_frames}:s={w}x{h}:fps={fps}"
            ),
            
            # Smooth zoom in from 1.0x to 1.3x (centered)
            "zoom_in": (
                f"scale=8000:-1,"
                f"zoompan=z='min(zoom+0.001,1.3)':"
                f"x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':"
                f"d={total_frames}:s={w}x{h}:fps={fps}"
            ),
            
            # Zoom out from 1.5x to 1.0x (reveals full image)
            "zoom_out": (
                f"scale=8000:-1,"
                f"zoompan=z='if(eq(on,1),1.5,max(zoom-0.002,1.0))':"
                f"x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':"
                f"d={total_frames}:s={w}x{h}:fps={fps}"
            ),
            
            # Pan from left to right
            "pan_left_to_right": (
                f"scale=-1:{h*2},"
                f"zoompan=z='1.2':"
                f"x='(iw-iw/zoom)*on/{total_frames}':"
                f"y='ih/2-(ih/zoom/2)':"
                f"d={total_frames}:s={w}x{h}:fps={fps}"
            ),
            
            # Pan from right to left
            "pan_right_to_left": (
                f"scale=-1:{h*2},"
                f"zoompan=z='1.2':"
                f"x='(iw-iw/zoom)*(1-on/{total_frames})':"
                f"y='ih/2-(ih/zoom/2)':"
                f"d={total_frames}:s={w}x{h}:fps={fps}"
            ),
            
            # Pan from top to bottom (good for tall infographics)
            "pan_top_to_bottom": (
                f"scale={w*2}:-1,"
                f"zoompan=z='1.2':"
                f"x='iw/2-(iw/zoom/2)':"
                f"y='(ih-ih/zoom)*on/{total_frames}':"
                f"d={total_frames}:s={w}x{h}:fps={fps}"
            ),
            
            # Static with subtle breathing zoom (keeps image alive)
            "static_breathe": (
                f"scale=8000:-1,"
                f"zoompan=z='1.05+0.02*sin(2*PI*on/{total_frames})':"
                f"x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)':"
                f"d={total_frames}:s={w}x{h}:fps={fps}"
            ),
            
            # No effect — just scale and hold
            "static": (
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
            ),
        }
        
        vf = effects.get(effect, effects["kenburns"])
        return vf

    def get_random_effect(self) -> str:
        """Returns a random cinematic effect (useful for variety across scenes)."""
        effects = ["kenburns", "zoom_in", "zoom_out", "pan_left_to_right", 
                   "pan_right_to_left", "static_breathe"]
        return random.choice(effects)

    # ─── CONCATENATION ────────────────────────────────────────────────────

    def _concatenate_scenes(self, clip_paths: List[str], output_path: str):
        """
        Concatenate multiple scene clips into one video.
        Uses FFmpeg concat demuxer (fast, no re-encoding if formats match).
        Falls back to concat filter for crossfades.
        """
        cf_dur = self.config.crossfade_duration
        
        if cf_dur <= 0 or len(clip_paths) <= 1:
            # Simple concatenation (fast, no re-encode)
            concat_file = os.path.join(self.config.temp_dir, "concat_list.txt")
            with open(concat_file, "w") as f:
                for path in clip_paths:
                    f.write(f"file '{os.path.abspath(path)}'\n")
            
            self._ffmpeg_run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", concat_file,
                "-c:v", self.config.codec,
                "-preset", self.config.preset,
                "-crf", str(self.config.crf),
                "-c:a", self.config.audio_codec,
                "-b:a", self.config.audio_bitrate,
                "-pix_fmt", "yuv420p",
                output_path
            ])
        else:
            # Concatenation with crossfade transitions
            # For many clips, we chain pairwise xfade filters
            self._concat_with_crossfade(clip_paths, output_path, cf_dur)

    def _concat_with_crossfade(self, clips: List[str], output: str, xfade_dur: float):
        """Build an FFmpeg xfade filter chain for smooth scene transitions."""
        n = len(clips)
        if n == 1:
            self._ffmpeg_run(["ffmpeg", "-y", "-i", clips[0], "-c", "copy", output])
            return

        # For simplicity with many clips, use concat demuxer (crossfade adds complexity)
        # This is a pragmatic choice — xfade filter chains get unwieldy beyond 10+ clips
        if n > 10:
            self.logger.info("  >10 clips: using simple concat (crossfade skipped)")
            concat_file = os.path.join(self.config.temp_dir, "concat_list.txt")
            with open(concat_file, "w") as f:
                for path in clips:
                    f.write(f"file '{os.path.abspath(path)}'\n")
            self._ffmpeg_run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
                "-c:v", self.config.codec, "-preset", self.config.preset,
                "-crf", str(self.config.crf),
                "-c:a", self.config.audio_codec, "-b:a", self.config.audio_bitrate,
                "-pix_fmt", "yuv420p", output
            ])
            return
        
        # Build xfade filter chain for ≤10 clips
        inputs = []
        for clip in clips:
            inputs.extend(["-i", clip])
        
        # Get durations for offset calculation
        durations = [self._get_video_duration(c) for c in clips]
        
        filter_parts = []
        offset = durations[0] - xfade_dur
        
        # First pair
        filter_parts.append(
            f"[0:v][1:v]xfade=transition=fade:duration={xfade_dur}:offset={offset:.3f}[v1]"
        )
        filter_parts.append(
            f"[0:a][1:a]acrossfade=d={xfade_dur}[a1]"
        )
        
        # Chain remaining clips
        for i in range(2, n):
            offset += durations[i] - xfade_dur
            prev_v = f"v{i-1}"
            prev_a = f"a{i-1}"
            curr_v = f"v{i}"
            curr_a = f"a{i}"
            filter_parts.append(
                f"[{prev_v}][{i}:v]xfade=transition=fade:duration={xfade_dur}:offset={offset:.3f}[{curr_v}]"
            )
            filter_parts.append(
                f"[{prev_a}][{i}:a]acrossfade=d={xfade_dur}[{curr_a}]"
            )
        
        last_v = f"v{n-1}"
        last_a = f"a{n-1}"
        filter_complex = ";".join(filter_parts)
        
        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", f"[{last_v}]", "-map", f"[{last_a}]",
            "-c:v", self.config.codec, "-preset", self.config.preset,
            "-crf", str(self.config.crf),
            "-c:a", self.config.audio_codec, "-b:a", self.config.audio_bitrate,
            "-pix_fmt", "yuv420p",
            output
        ]
        self._ffmpeg_run(cmd)

    # ─── SUBTITLES ────────────────────────────────────────────────────────

    def _generate_srt_from_scenes(self, scenes: List[Scene]) -> str:
        """
        Generate an SRT subtitle file from scene text and audio timing.
        
        Each scene's text is split into chunks of max_chars_per_line for readability.
        Timing is distributed evenly across chunks within each scene's duration.
        """
        srt_path = os.path.join(self.config.temp_dir, "subtitles.srt")
        srt_lines = []
        counter = 1
        cumulative_time = 0.0
        max_chars = self.config.max_chars_per_line
        
        for scene in scenes:
            if not scene.text.strip():
                cumulative_time += scene.duration
                continue
            
            # Split text into subtitle chunks
            words = scene.text.split()
            chunks = []
            current_chunk = []
            current_len = 0
            
            for word in words:
                if current_len + len(word) + 1 > max_chars and current_chunk:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = [word]
                    current_len = len(word)
                else:
                    current_chunk.append(word)
                    current_len += len(word) + 1
            
            if current_chunk:
                chunks.append(" ".join(current_chunk))
            
            # Distribute timing across chunks
            if chunks:
                chunk_duration = scene.duration / len(chunks)
                for i, chunk in enumerate(chunks):
                    start = cumulative_time + (i * chunk_duration)
                    end = start + chunk_duration
                    
                    srt_lines.append(str(counter))
                    srt_lines.append(
                        f"{self._format_srt_time(start)} --> {self._format_srt_time(end)}"
                    )
                    srt_lines.append(chunk)
                    srt_lines.append("")
                    counter += 1
            
            cumulative_time += scene.duration
        
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))
        
        self.logger.info(f"  Generated SRT: {counter - 1} subtitle entries")
        return srt_path

    def _burn_subtitles(self, input_path: str, srt_path: str, output_path: str):
        """
        Burn (hardcode) SRT subtitles into the video using FFmpeg's subtitles filter.
        
        Styling uses ASS/SSA force_style for maximum control over appearance.
        """
        # Build ASS force_style string
        # Reference: https://fileformats.fandom.com/wiki/SubStation_Alpha
        font_size = self.config.subtitle_font_size
        margin_v = self.config.subtitle_margin_bottom
        
        # Color format for ASS is &HBBGGRR& (BGR, not RGB)
        style = (
            f"FontName={self.config.subtitle_font},"
            f"FontSize={font_size},"
            f"PrimaryColour=&H00FFFFFF,"   # White text
            f"OutlineColour=&H00000000,"   # Black outline
            f"BackColour=&H80000000,"      # Semi-transparent black shadow
            f"Bold=1,"
            f"Outline=2,"                  # Outline thickness
            f"Shadow=1,"                   # Shadow depth
            f"MarginV={margin_v},"         # Bottom margin
            f"Alignment=2"                 # Bottom center
        )
        
        # Escape the SRT path for FFmpeg filter (colons and backslashes)
        srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
        
        vf = f"subtitles='{srt_escaped}':force_style='{style}'"
        
        self._ffmpeg_run([
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", vf,
            "-c:v", self.config.codec,
            "-preset", self.config.preset,
            "-crf", str(self.config.crf),
            "-c:a", "copy",
            "-pix_fmt", "yuv420p",
            output_path
        ])
        self.logger.info("  Subtitles burned successfully")

    # ─── BACKGROUND MUSIC ─────────────────────────────────────────────────

    def _mix_background_music(
        self, video_path: str, music_path: str, output_path: str, video_duration: float
    ):
        """
        Mix background music with the video's narration audio.
        
        The music is:
            - Looped if shorter than the video
            - Faded in (2s) and faded out (3s)
            - Volume-reduced to bg_music_volume level
            - Mixed under the existing narration track
        """
        vol = self.config.bg_music_volume
        fade_in = 2.0
        fade_out = 3.0
        fade_out_start = max(0, video_duration - fade_out)
        
        # Audio filter: loop music, apply volume + fades, mix with original
        filter_complex = (
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=duration={video_duration},"
            f"volume={vol},"
            f"afade=t=in:st=0:d={fade_in},"
            f"afade=t=out:st={fade_out_start:.1f}:d={fade_out}[music];"
            f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        
        self._ffmpeg_run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", music_path,
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", self.config.audio_codec,
            "-b:a", self.config.audio_bitrate,
            output_path
        ])
        self.logger.info(f"  Background music mixed (volume={vol})")

    # ─── WATERMARK ────────────────────────────────────────────────────────

    def _add_watermark(self, input_path: str, output_path: str):
        """Overlay a semi-transparent watermark/logo on the video."""
        wm = self.config.watermark_path
        opacity = self.config.watermark_opacity
        pos = self.config.watermark_position
        
        # Position mapping (with 20px padding)
        positions = {
            "top-right": "W-w-20:20",
            "top-left": "20:20",
            "bottom-right": "W-w-20:H-h-20",
            "bottom-left": "20:H-h-20",
        }
        overlay_pos = positions.get(pos, positions["top-right"])
        
        filter_complex = (
            f"[1:v]format=rgba,colorchannelmixer=aa={opacity}[wm];"
            f"[0:v][wm]overlay={overlay_pos}[vout]"
        )
        
        self._ffmpeg_run([
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", wm,
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "0:a",
            "-c:v", self.config.codec, "-preset", self.config.preset,
            "-crf", str(self.config.crf),
            "-c:a", "copy",
            output_path
        ])
        self.logger.info("  Watermark added")

    # ─── THUMBNAIL GENERATOR ──────────────────────────────────────────────

    def generate_thumbnail(
        self,
        background_image: str,
        title_text: str,
        output_path: str,
        font_size: int = 72,
        text_color: str = "white",
        outline_color: str = "black",
    ) -> str:
        """
        Generate a YouTube thumbnail (1280x720) with text overlay.
        
        Uses Pillow for text rendering with outline effect.
        For production, consider using DALL-E or Canva API instead.
        """
        thumb_w, thumb_h = 1280, 720
        
        # Load and resize background
        img = Image.open(background_image)
        img = img.resize((thumb_w, thumb_h), Image.LANCZOS)
        
        # Add dark gradient overlay for text readability
        gradient = Image.new("RGBA", (thumb_w, thumb_h), (0, 0, 0, 0))
        draw_grad = ImageDraw.Draw(gradient)
        for y in range(thumb_h):
            alpha = int(180 * (y / thumb_h))  # darker at bottom
            draw_grad.rectangle([(0, y), (thumb_w, y + 1)], fill=(0, 0, 0, alpha))
        
        img = img.convert("RGBA")
        img = Image.alpha_composite(img, gradient)
        
        # Draw text with outline
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(self.config.font_path, font_size)
        except (IOError, OSError):
            font = ImageFont.load_default()
        
        # Word wrap
        lines = self._wrap_text(title_text, font, thumb_w - 100, draw)
        
        # Calculate text position (centered, lower third)
        line_height = font_size + 10
        total_text_height = len(lines) * line_height
        y_start = thumb_h - total_text_height - 80
        
        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
            x = (thumb_w - text_w) // 2
            y = y_start + i * line_height
            
            # Draw outline
            for dx in range(-3, 4):
                for dy in range(-3, 4):
                    draw.text((x + dx, y + dy), line, font=font, fill=outline_color)
            # Draw main text
            draw.text((x, y), line, font=font, fill=text_color)
        
        # Save
        img = img.convert("RGB")
        img.save(output_path, "JPEG", quality=95)
        self.logger.info(f"  Thumbnail generated: {output_path}")
        return output_path

    # ─── SHORTS FORMAT ────────────────────────────────────────────────────

    def assemble_short(
        self,
        scenes: List[Scene],
        output_path: str,
        bg_music_path: Optional[str] = None,
    ) -> str:
        """
        Assemble a YouTube Shorts video (9:16, 1080x1920, ≤60s).
        
        Overrides config for vertical format and larger subtitles.
        """
        # Save original config
        orig_w, orig_h = self.config.width, self.config.height
        orig_font = self.config.subtitle_font_size
        orig_margin = self.config.subtitle_margin_bottom
        
        # Set Shorts config
        self.config.width = 1080
        self.config.height = 1920
        self.config.subtitle_font_size = 64
        self.config.subtitle_margin_bottom = 200
        
        try:
            result = self.assemble(scenes, output_path, bg_music_path)
        finally:
            # Restore original config
            self.config.width = orig_w
            self.config.height = orig_h
            self.config.subtitle_font_size = orig_font
            self.config.subtitle_margin_bottom = orig_margin
        
        return result

    # ─── UTILITY METHODS ──────────────────────────────────────────────────

    def _get_audio_duration(self, path: str) -> float:
        """Get audio file duration in seconds using ffprobe."""
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True
        )
        return float(result.stdout.strip())

    def _get_video_duration(self, path: str) -> float:
        """Get video file duration in seconds using ffprobe."""
        return self._get_audio_duration(path)  # same ffprobe command works

    def _format_srt_time(self, seconds: float) -> str:
        """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _wrap_text(self, text: str, font, max_width: int, draw) -> List[str]:
        """Word-wrap text to fit within max_width pixels."""
        words = text.split()
        lines = []
        current = []
        for word in words:
            test = " ".join(current + [word])
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] > max_width and current:
                lines.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            lines.append(" ".join(current))
        return lines

    def _ffmpeg_run(self, cmd: List[str]):
        """Execute an FFmpeg command with error handling."""
        self.logger.debug(f"  CMD: {' '.join(cmd[:10])}...")
        result = subprocess.run(
            cmd, capture_output=True, text=True
        )
        if result.returncode != 0:
            self.logger.error(f"FFmpeg error:\n{result.stderr[-2000:]}")
            raise RuntimeError(f"FFmpeg failed (code {result.returncode}): {result.stderr[-500:]}")

    def cleanup_temp(self):
        """Remove temporary files from the working directory."""
        import shutil
        if os.path.exists(self.config.temp_dir):
            shutil.rmtree(self.config.temp_dir)
            self.logger.info("  Temp directory cleaned")


# ─── WHISPER INTEGRATION ──────────────────────────────────────────────────────

class WhisperSubtitleGenerator:
    """
    Generate word-level SRT subtitles from audio using OpenAI Whisper.
    
    This produces more accurate subtitles than the simple scene-text-based
    approach above, because it uses actual speech timing.
    
    Usage:
        gen = WhisperSubtitleGenerator(model_size="base")
        srt_path = gen.generate("full_narration.mp3", "output.srt")
    """
    
    def __init__(self, model_size: str = "base"):
        """
        Initialize Whisper model.
        
        model_size: tiny/base/small/medium/large
            - tiny/base: fast, good for English
            - small/medium: slower, better accuracy + multilingual
            - large: best accuracy, needs GPU
        """
        self.model_size = model_size
        self.model = None
    
    def _load_model(self):
        """Lazy-load the Whisper model."""
        if self.model is None:
            import whisper
            self.model = whisper.load_model(self.model_size)
    
    def generate(self, audio_path: str, output_srt: str, language: str = "en") -> str:
        """
        Transcribe audio and generate SRT subtitle file.
        
        Returns path to the generated SRT file.
        """
        self._load_model()
        
        result = self.model.transcribe(
            audio_path,
            language=language,
            word_timestamps=True,
            verbose=False
        )
        
        # Build SRT from word-level timestamps, grouped into readable chunks
        srt_entries = []
        counter = 1
        
        for segment in result["segments"]:
            words = segment.get("words", [])
            if not words:
                # Fallback to segment-level timing
                srt_entries.append({
                    "index": counter,
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": segment["text"].strip()
                })
                counter += 1
                continue
            
            # Group words into chunks of ~6-10 words
            chunk = []
            chunk_start = None
            
            for word_info in words:
                if chunk_start is None:
                    chunk_start = word_info["start"]
                chunk.append(word_info["word"].strip())
                
                if len(chunk) >= 8 or word_info == words[-1]:
                    srt_entries.append({
                        "index": counter,
                        "start": chunk_start,
                        "end": word_info["end"],
                        "text": " ".join(chunk)
                    })
                    counter += 1
                    chunk = []
                    chunk_start = None
        
        # Write SRT file
        self._write_srt(srt_entries, output_srt)
        return output_srt
    
    def _write_srt(self, entries: List[Dict], path: str):
        """Write entries to SRT format."""
        lines = []
        for entry in entries:
            lines.append(str(entry["index"]))
            start = self._seconds_to_srt(entry["start"])
            end = self._seconds_to_srt(entry["end"])
            lines.append(f"{start} --> {end}")
            lines.append(entry["text"])
            lines.append("")
        
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    
    def _seconds_to_srt(self, s: float) -> str:
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = int(s % 60)
        ms = int((s % 1) * 1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


# ─── CONVENIENCE: FULL PIPELINE FUNCTION ──────────────────────────────────────

def assemble_faceless_video(
    scenes_data: List[Dict],
    output_filename: str = "final_video.mp4",
    bg_music_path: Optional[str] = None,
    config: Optional[VideoConfig] = None,
    use_whisper_subs: bool = False,
    shorts_mode: bool = False,
) -> str:
    """
    High-level convenience function for the full pipeline.
    
    Args:
        scenes_data: List of dicts with keys:
            - image_path: str (path to scene image)
            - audio_path: str (path to TTS audio)
            - text: str (script text for subtitles)
            - effect: str (optional, e.g. "kenburns", "zoom_in")
        output_filename: Name of the output file
        bg_music_path: Optional path to background music
        config: Optional VideoConfig override
        use_whisper_subs: If True, use Whisper for subtitle timing
        shorts_mode: If True, render in 9:16 vertical format
    
    Returns:
        Path to the final rendered video
    
    Example:
        >>> scenes = [
        ...     {
        ...         "image_path": "assets/scene_0.png",
        ...         "audio_path": "assets/audio_0.mp3",
        ...         "text": "Artificial intelligence is transforming how we create content.",
        ...         "effect": "kenburns"
        ...     },
        ...     {
        ...         "image_path": "assets/scene_1.png",
        ...         "audio_path": "assets/audio_1.mp3",
        ...         "text": "In 2026, faceless YouTube channels are generating millions of views.",
        ...         "effect": "zoom_in"
        ...     }
        ... ]
        >>> result = assemble_faceless_video(scenes, "my_video.mp4", "lofi_beat.mp3")
    """
    cfg = config or VideoConfig()
    assembler = VideoAssembler(cfg)
    
    # Convert dict data to Scene objects
    scenes = []
    for i, s in enumerate(scenes_data):
        effect = s.get("effect") or assembler.get_random_effect()
        scenes.append(Scene(
            scene_id=i,
            image_path=s["image_path"],
            audio_path=s["audio_path"],
            text=s.get("text", ""),
            effect=effect,
        ))
    
    # Generate Whisper-based subtitles if requested
    srt_path = None
    if use_whisper_subs:
        # First concatenate all audio to get full narration
        audio_concat = os.path.join(cfg.temp_dir, "full_narration.mp3")
        concat_file = os.path.join(cfg.temp_dir, "audio_list.txt")
        os.makedirs(cfg.temp_dir, exist_ok=True)
        with open(concat_file, "w") as f:
            for scene in scenes:
                f.write(f"file '{os.path.abspath(scene.audio_path)}'\n")
        subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_file, "-c", "copy", audio_concat
        ], capture_output=True)
        
        whisper_gen = WhisperSubtitleGenerator(model_size="base")
        srt_path = whisper_gen.generate(audio_concat, os.path.join(cfg.temp_dir, "whisper.srt"))
    
    # Assemble
    if shorts_mode:
        return assembler.assemble_short(scenes, output_filename, bg_music_path)
    else:
        return assembler.assemble(
            scenes=scenes,
            output_path=output_filename,
            bg_music_path=bg_music_path,
            subtitle_srt_path=srt_path,
        )


# ─── ENTRY POINT FOR TESTING ─────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    
    print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║           VIDEO ASSEMBLER — Faceless YouTube Pipeline       ║
    ╠══════════════════════════════════════════════════════════════╣
    ║                                                              ║
    ║  Usage Example:                                              ║
    ║                                                              ║
    ║    from video_assembler import assemble_faceless_video       ║
    ║                                                              ║
    ║    scenes = [                                                ║
    ║      {"image_path": "img.png",                               ║
    ║       "audio_path": "audio.mp3",                             ║
    ║       "text": "Your narration text here"},                   ║
    ║    ]                                                         ║
    ║                                                              ║
    ║    assemble_faceless_video(scenes, "output.mp4")             ║
    ║                                                              ║
    ║  Effects: kenburns, zoom_in, zoom_out, pan_left_to_right,   ║
    ║           pan_right_to_left, pan_top_to_bottom,              ║
    ║           static_breathe, static                             ║
    ║                                                              ║
    ╚══════════════════════════════════════════════════════════════╝
    """)
