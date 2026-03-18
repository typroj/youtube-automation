"""
EXAMPLE: Full Pipeline — Topic → Script → TTS → Images → Video
================================================================

This example shows how to wire the video assembler into your
complete faceless YouTube automation pipeline.

Replace the placeholder API calls with your actual API keys.
"""

import os
import json
import logging
from video_assembler import (
    VideoConfig, VideoAssembler, Scene,
    assemble_faceless_video, WhisperSubtitleGenerator
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("Pipeline")


# ═══════════════════════════════════════════════════════════════
# STEP 1: Script Generation (replace with your LLM API call)
# ═══════════════════════════════════════════════════════════════

def generate_script(topic: str, num_scenes: int = 8) -> list[dict]:
    """
    Call your LLM (Claude/GPT/Gemini) to generate a scene-by-scene script.
    
    Your prompt should ask for JSON output with this structure:
    [
        {"scene_id": 0, "narration": "...", "visual_description": "..."},
        {"scene_id": 1, "narration": "...", "visual_description": "..."},
    ]
    """
    # ── PLACEHOLDER: Replace with actual API call ──
    # Example with Claude API:
    #
    # import anthropic
    # client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    # response = client.messages.create(
    #     model="claude-sonnet-4-20250514",
    #     max_tokens=4000,
    #     system="You are a YouTube scriptwriter. Output JSON only.",
    #     messages=[{"role": "user", "content": f"""
    #         Write a {num_scenes}-scene script about: {topic}
    #         Format: [{{"scene_id": 0, "narration": "...", "visual_description": "..."}}]
    #     """}]
    # )
    # return json.loads(response.content[0].text)
    
    # Demo data for testing:
    return [
        {
            "scene_id": 0,
            "narration": "In 2026, artificial intelligence is no longer a futuristic concept. It is reshaping every industry on the planet.",
            "visual_description": "Futuristic city skyline with AI neural network overlay"
        },
        {
            "scene_id": 1,
            "narration": "From healthcare to finance, from education to entertainment, AI tools are automating tasks that once took humans hours.",
            "visual_description": "Split screen showing AI applications in different industries"
        },
        {
            "scene_id": 2,
            "narration": "But here is what most people do not realize. The real revolution is not in the technology itself. It is in who gets to use it.",
            "visual_description": "Person at laptop with AI dashboard, dramatic lighting"
        },
        {
            "scene_id": 3,
            "narration": "Today, a single creator with the right tools can produce content that used to require an entire production team.",
            "visual_description": "Content creation workspace with multiple screens showing video editing"
        },
    ]


# ═══════════════════════════════════════════════════════════════
# STEP 2: TTS Audio Generation
# ═══════════════════════════════════════════════════════════════

def generate_tts_audio(scenes: list[dict], output_dir: str) -> list[str]:
    """
    Generate TTS audio for each scene using ElevenLabs (or your TTS provider).
    Returns list of audio file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    audio_paths = []
    
    for scene in scenes:
        audio_path = os.path.join(output_dir, f"audio_{scene['scene_id']:03d}.mp3")
        
        # ── PLACEHOLDER: Replace with actual ElevenLabs API call ──
        # from elevenlabs import ElevenLabs
        # client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
        # audio = client.text_to_speech.convert(
        #     voice_id="pNInz6obpgDQGcFmaJgB",  # "Adam" voice
        #     model_id="eleven_multilingual_v2",
        #     text=scene["narration"],
        # )
        # with open(audio_path, "wb") as f:
        #     for chunk in audio:
        #         f.write(chunk)
        
        audio_paths.append(audio_path)
        logger.info(f"  TTS: Scene {scene['scene_id']} → {audio_path}")
    
    return audio_paths


# ═══════════════════════════════════════════════════════════════
# STEP 3: Image Generation
# ═══════════════════════════════════════════════════════════════

def generate_images(scenes: list[dict], output_dir: str) -> list[str]:
    """
    Generate images for each scene using DALL-E, Stable Diffusion, or Pexels.
    Returns list of image file paths.
    """
    os.makedirs(output_dir, exist_ok=True)
    image_paths = []
    
    for scene in scenes:
        image_path = os.path.join(output_dir, f"scene_{scene['scene_id']:03d}.png")
        
        # ── OPTION A: DALL-E 3 ──
        # from openai import OpenAI
        # client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        # response = client.images.generate(
        #     model="dall-e-3",
        #     prompt=scene["visual_description"],
        #     size="1792x1024",  # Landscape for YouTube
        #     quality="standard",
        #     n=1,
        # )
        # # Download and save image...
        
        # ── OPTION B: Pexels Stock Footage ──
        # import requests
        # headers = {"Authorization": os.environ["PEXELS_API_KEY"]}
        # query = scene["visual_description"][:50]
        # resp = requests.get(
        #     f"https://api.pexels.com/v1/search?query={query}&per_page=1&orientation=landscape",
        #     headers=headers
        # )
        # photo_url = resp.json()["photos"][0]["src"]["large2x"]
        # # Download and save...
        
        image_paths.append(image_path)
        logger.info(f"  Image: Scene {scene['scene_id']} → {image_path}")
    
    return image_paths


# ═══════════════════════════════════════════════════════════════
# STEP 4: Assemble Video
# ═══════════════════════════════════════════════════════════════

def run_full_pipeline():
    """Run the complete faceless video pipeline."""
    
    topic = "How AI is Changing Content Creation in 2026"
    logger.info(f"═══ Starting Pipeline: {topic} ═══")
    
    # Generate script
    logger.info("Step 1: Generating script...")
    script_scenes = generate_script(topic, num_scenes=4)
    
    # Generate TTS audio
    logger.info("Step 2: Generating TTS audio...")
    audio_paths = generate_tts_audio(script_scenes, "assets/audio")
    
    # Generate images
    logger.info("Step 3: Generating images...")
    image_paths = generate_images(script_scenes, "assets/images")
    
    # Prepare scenes data for assembler
    scenes_data = []
    effects = ["kenburns", "zoom_in", "pan_left_to_right", "zoom_out",
               "static_breathe", "pan_right_to_left"]
    
    for i, scene in enumerate(script_scenes):
        scenes_data.append({
            "image_path": image_paths[i],
            "audio_path": audio_paths[i],
            "text": scene["narration"],
            "effect": effects[i % len(effects)],  # Cycle through effects
        })
    
    # Configure video
    config = VideoConfig(
        width=1920,
        height=1080,
        fps=30,
        preset="medium",
        crf=20,
        default_image_effect="kenburns",
        crossfade_duration=0.5,
        subtitle_font_size=52,
        bg_music_volume=0.08,
    )
    
    # Assemble!
    logger.info("Step 4: Assembling video...")
    output = assemble_faceless_video(
        scenes_data=scenes_data,
        output_filename="ai_content_creation_2026.mp4",
        bg_music_path="assets/music/lofi_background.mp3",  # Your royalty-free music
        config=config,
        use_whisper_subs=False,    # Set True for Whisper-based subtitles
        shorts_mode=False,         # Set True for YouTube Shorts (9:16)
    )
    
    logger.info(f"═══ Pipeline Complete: {output} ═══")
    
    # ── STEP 5 (OPTIONAL): Generate Thumbnail ──
    assembler = VideoAssembler(config)
    assembler.generate_thumbnail(
        background_image=image_paths[0],
        title_text="How AI Is Changing Content Creation in 2026",
        output_path="output/thumbnail.jpg"
    )
    
    # ── STEP 6 (OPTIONAL): Upload to YouTube ──
    # from google_auth_oauthlib.flow import InstalledAppFlow
    # from googleapiclient.discovery import build
    # from googleapiclient.http import MediaFileUpload
    #
    # youtube = build("youtube", "v3", credentials=creds)
    # request = youtube.videos().insert(
    #     part="snippet,status",
    #     body={
    #         "snippet": {
    #             "title": "How AI Is Changing Content Creation in 2026",
    #             "description": "...",
    #             "tags": ["AI", "content creation", "2026"],
    #             "categoryId": "28"  # Science & Technology
    #         },
    #         "status": {"privacyStatus": "public"}
    #     },
    #     media_body=MediaFileUpload(output)
    # )
    # response = request.execute()


# ═══════════════════════════════════════════════════════════════
# EXAMPLE: Quick Single-Video Assembly (for testing)
# ═══════════════════════════════════════════════════════════════

def quick_test():
    """
    Minimal test — assemble 2 scenes from existing assets.
    
    Before running, create test assets:
        mkdir -p test_assets
        # Place test_scene_0.png, test_scene_1.png (any 1920x1080 images)
        # Place test_audio_0.mp3, test_audio_1.mp3 (any audio clips)
    """
    config = VideoConfig(
        width=1920, height=1080, fps=30,
        preset="ultrafast",  # Fast for testing
        crf=23,
        crossfade_duration=0,  # No crossfade for quick test
    )
    
    assembler = VideoAssembler(config)
    
    scenes = [
        Scene(
            scene_id=0,
            image_path="test_assets/test_scene_0.png",
            audio_path="test_assets/test_audio_0.mp3",
            text="This is the first scene of our test video.",
            effect="kenburns"
        ),
        Scene(
            scene_id=1,
            image_path="test_assets/test_scene_1.png",
            audio_path="test_assets/test_audio_1.mp3",
            text="And this is the second scene with a zoom effect.",
            effect="zoom_in"
        ),
    ]
    
    output = assembler.assemble(
        scenes=scenes,
        output_path="test_output.mp4",
    )
    print(f"Test video: {output}")


if __name__ == "__main__":
    # Uncomment the one you want to run:
    # quick_test()
    run_full_pipeline()
