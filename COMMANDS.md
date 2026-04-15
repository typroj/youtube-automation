# FutureProof AI — Pipeline Command Reference

> All commands run from: `C:\youtube-automation\`
> Activate venv first: `venv\Scripts\activate`

---

## NICHES

| Niche Key | Topic |
|-----------|-------|
| `ai_tools` | AI tools, ChatGPT, Claude, Cursor, automation |
| `tech` | General technology news |
| `tech_gadgets` | 2026 gadgets — phones, drones, cameras, wearables |
| `finance` | Money, investing, markets |
| `self_improvement` | Productivity, mindset, habits |
| `health` | Wellness, fitness, nutrition |
| `history` | Historical events and untold stories |
| `world_crisis` | Geopolitics, war, global conflicts |
| `heritage` | India's Lost Hindu-Buddhist Heritage (90-day series) — **vintage effect auto-enabled** |

---

## VIDEO BACKGROUND MODE

| Mode | Flag | Background | Speed | Best For |
|------|------|-----------|-------|----------|
| **Images** (default) | *(nothing)* | Still photos with Ken Burns zoom/pan effect | ~5-7 min | All niches |
| **Video Clips** | `--use-videos` | Live MP4 clips from Pexels | ~3-4 min | More dynamic reels |

```bash
# With images (default)
python orchestrator.py --reels --niche ai_tools --count 1 --topic "Your topic"

# With video clips
python orchestrator.py --reels --niche ai_tools --count 1 --topic "Your topic" --use-videos
```

---

## BASIC MODES

### 1. Auto Reel (topic auto-discovered)
```bash
python orchestrator.py --reels --niche ai_tools --count 1
```

### 2. Manual Topic Reel (Hinglish script + video clips)
```bash
python orchestrator.py --reels --niche ai_tools --count 1 --topic "Your topic here" --use-videos
```

### 3. Manual Topic Reel (images, not video clips)
```bash
python orchestrator.py --reels --niche world_crisis --count 1 --topic "Your topic here"
```

### 4. Custom Script File (your own content — no AI script generation)
```bash
python orchestrator.py --reels --niche ai_tools --script-file scripts/my_script.txt
python orchestrator.py --reels --niche ai_tools --script-file scripts/my_script.txt --use-videos
```

### 5. Long-form YouTube Video
```bash
python orchestrator.py --niche ai_tools --count 1
```

### 6. Multiple Reels (same niche)
```bash
python orchestrator.py --reels --niche ai_tools --count 3
```

### 7. Multiple Reels (different niches, one per niche)
```bash
python orchestrator.py --reels --multi-niche ai_tools,world_crisis,tech_gadgets
```

---

## FLAGS & OPTIONS

| Flag | Values | Description |
|------|--------|-------------|
| `--niche` | see niche table above | Which content niche to use |
| `--count` | any number | How many videos/reels to generate |
| `--topic` | "any text" | Skip discovery, use this exact topic |
| `--script-file` | `"path/to/file.txt"` | Provide your own full script — skips topic discovery AND script generation |
| `--use-videos` | (no value) | Use Pexels video clips instead of images |
| `--reels` | (no value) | Generate vertical reels (1080×1920) |
| `--multi-niche` | `niche1,niche2` | Generate one reel per niche listed |
| `--length` | minutes (e.g. `8`) | Long-form video length in minutes |
| `--privacy` | `public` / `private` / `unlisted` | YouTube upload privacy |
| `--provider` | `anthropic` / `openai` / `gemini` | LLM for script writing |
| `--tts-provider` | `elevenlabs` / `minimax` / `openai` | TTS voice engine |
| `--vintage` | (no value) | Apply old-film effect (grain, sepia, vignette). Auto-enabled for `heritage` niche |
| `--dry-run` | (no value) | Generate script only, skip video+upload |
| `--verbose` | (no value) | Extra detailed logs |

---

## SCRIPT FILE FORMAT  (`--script-file`)

Create a `.txt` file in `scripts/` with these exact section headers:

```
[TITLE]
Claude AI ka Secret Source Code Leak — Sach Jaano!

[HOOK]
Yaar, kya tumhe pata hai Claude AI ke andar kya chal raha hai?

[BODY]
Main body content — 60 to 120 words in Hinglish...

[CLOSER]
Agar ye jaanke hairan ho gaye toh comment karo "SHOCKED"!

[HASHTAGS]
#AI #ClaudeAI #ArtificialIntelligence #TechNews
```

Then run:
```bash
# Images background (default)
python orchestrator.py --reels --niche ai_tools --script-file scripts/my_script.txt

# Video clips background
python orchestrator.py --reels --niche ai_tools --script-file scripts/my_script.txt --use-videos

# Private first (review before posting)
python orchestrator.py --reels --niche ai_tools --script-file scripts/my_script.txt --privacy private
```

> A sample is provided at `scripts/sample_script.txt`.

---

## UTILITY COMMANDS

### List all generated videos
```bash
python orchestrator.py --list-outputs
```

### Discover trending topics only (no video)
```bash
python orchestrator.py --discover-only 5 --niche ai_tools
```

### Re-upload an existing video
```bash
python orchestrator.py --retry-upload output/20260401_211120_reel00.mp4
```

### Re-upload with custom title
```bash
python orchestrator.py --retry-upload output/20260401_211120_reel00.mp4 --title "My Custom Title"
```

### Re-upload to specific platforms only
```bash
python orchestrator.py --retry-upload output/video.mp4 --platforms instagram,youtube
```

### Refresh Instagram analytics
```bash
python orchestrator.py --refresh-analytics
```

### View analytics report
```bash
python orchestrator.py --analytics-report
```

### Run without analytics weighting
```bash
python orchestrator.py --reels --niche ai_tools --count 1 --no-analytics
```

---

## COMMON COMBINATIONS

### Heritage series reel (vintage effect auto-applied, historical clips)
```bash
python orchestrator.py --reels --niche heritage --script-file scripts/day1.txt --use-videos
```

### Viral Hinglish reel with video background
```bash
python orchestrator.py --reels --niche ai_tools --count 1 --topic "GPT-5 launch" --use-videos
```

### World crisis reel, private (review before posting)
```bash
python orchestrator.py --reels --niche world_crisis --count 1 --topic "Your topic" --privacy private
```

### Test script only (no video rendering, no upload)
```bash
python orchestrator.py --reels --niche ai_tools --count 1 --topic "Test topic" --dry-run
```

### Use MiniMax TTS instead of ElevenLabs
```bash
python orchestrator.py --reels --niche ai_tools --count 1 --tts-provider minimax
```

### Use OpenAI GPT for scripting instead of Claude
```bash
python orchestrator.py --reels --niche ai_tools --count 1 --provider openai
```

### Long-form 10-minute video, unlisted
```bash
python orchestrator.py --niche ai_tools --count 1 --length 10 --privacy unlisted
```

---

## CURRENT .ENV DEFAULTS

| Setting | Current Value |
|---------|--------------|
| TTS Provider | ElevenLabs |
| Voice | Project-awarness (`7u0fP0Vnw5n2WKcDQTsa`) |
| TTS Model | `eleven_v3` |
| LLM | Anthropic (Claude) |
| Script Language | Hinglish |
| Upload Privacy | Public |
| Niche (default) | ai_tools |

---

## OUTPUT LOCATIONS

| Item | Path |
|------|------|
| Final videos | `output/` |
| Temp files | `tmp/` |
| Logs | `logs/` |
| Background music | `assets/music/` |
| Subtitles | `tmp/{run_id}/subtitles.ass` |
| Script archive | `scripts_archive/` |

---

## NOTES

- **Hinglish**: All scripts are generated in Hinglish (Hindi+English mix, Roman script). ElevenLabs reads it correctly.
- **Video clips vs images**: `--use-videos` fetches MP4 clips from Pexels (more dynamic, faster render). Without the flag, it uses still images with Ken Burns effect.
- **Whisper subtitles**: Subtitles are auto-synced to actual speech using `faster-whisper`. They will be in Hinglish since they transcribe what is spoken.
- **Facebook upload**: Currently failing (`'video_id'` bug) — YouTube and Instagram work fine.
- **Windows console**: Unicode (emojis, Hindi chars) in logs are handled automatically.