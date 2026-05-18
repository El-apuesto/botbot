# Twin Shadow

An esoteric/occult comedy content platform: Python Flask web server + Telegram bot with a retro DOS-style UI, multi-model AI hierarchy, boardroom sessions, brainstorming, TTS multi-voice audio, a Builder bot, and a Video Lab.

## Architecture

- **Flask web server** — port 5000, started by `part6_bot.py` at import time in a daemon thread
- **Telegram bot** — runs concurrently via `python-telegram-bot` async loop; requires `TOKEN` env var
- **Web UI** — `static/index.html` — IBM Plex Mono retro interface; 7-tab nav; chat panel, desktop background
- **Video Lab** — `static/lab.html` — `/lab` route; same aesthetic; full short-form video pipeline
- **AI streaming** — all API calls are async; Flask↔async bridge uses a Queue + daemon thread per request for real SSE streaming

## Authority Hierarchy (LOCKED)

- **CAPI** = Ollama Cloud `leckminartor/qwen3.5-uncensored` — true top authority; known only to TWIN and SHADOW
- **SHADOW** = OpenRouter Dolphin Mistral Venice — what all other bots believe is top authority
- **TWIN** = Groq Llama 3.3 70B — primary chat voice; defers to SHADOW silently
- **Board members** = Hermes 3, GPT-OSS-120B, Qwen 3 235B, Mistral 7B, MiniMax — report to TWIN only, unaware of CAPI

## Project Structure

All source files inside `twin shadow flat/`:

| File | Purpose |
|---|---|
| `main.py` | Entry point |
| `part1_registry.py` | Model registry, TTS voice assignments, board/brainstorm member lists |
| `part2_router.py` | Async LLM router with key rotation (Groq ×3, AIML ×3) |
| `part3_modules.py` | AI text modules (brief, code review, creative, business, shadow fallback) |
| `part4_video.py` | Video generation (FAL/Replicate/HuggingFace) + FFMPEG utilities (extract_last_frame, concatenate_clips, mix_audio_onto_video, etc.) |
| `part5_orchestrator.py` | Execution engine, vault, RAG, cross-module validation |
| `part6_bot.py` | Flask server + all API routes + Telegram bot handlers |
| `part7_commands.py` | Telegram bot command handlers |
| `part8_personas.py` | System prompts: CAPI, SHADOW, TWIN, board members, brainstorm, builder |
| `static/index.html` | Full DOS-style web UI (chat, boardroom, brainstorm, pipeline, settings) |
| `static/lab.html` | Video Lab page (/lab) — storyboard, image gen, video gen, voiceover, FFMPEG render |
| `static/desk_bg.jpg` | Desktop background image |
| `static/mobile_bg.jpg` | Mobile background image |
| `static/send_btn.jpg` | Send button image |
| `static/music/` | Music library for video lab (MP3/WAV/OGG) |
| `audio/` | TTS-rendered MP3 output files + voiceovers |
| `uploads/` | Lab uploads (video, audio, image files) |
| `renders/` | Final rendered MP4 output files |

## Nav Structure (8 tabs)

| Tab | Key | Content |
|---|---|---|
| HOME | 1 | Main chat (TWIN / SHADOW) + [DASH] pipeline popup |
| COMMITTEES | 2 | MISSIONS + COMMITTEE builder + BOARDROOM (multi-agent board sessions) |
| LABS | 3 | VIDEO LAB (link to /lab) + WRITE (Bible/Reader/Book) + CODE (Builder/Architect) |
| SOCIAL | 4 | Social media content generation |
| PODCASTS | 5 | PODCAST (full character + cast format system) + CHARS |
| BRAINSTORM | 6 | Standalone brainstorm/riff sessions |
| LEGAL/MKT | 7 | LEGAL + MARKETING advisor chat |
| SETTINGS | 8 | User preferences, API settings |

## Color Palette (BBS/ANSI — no purple)

| Var | Value | Use |
|---|---|---|
| `--cyan` | `#00cccc` | Primary accent, borders |
| `--cyan-dim` | `#003333` | Subtle backgrounds |
| `--cyan-dark` | `#000d0d` | Dark tinted bg |
| `--cyan-glow` | `rgba(0,200,200,0.45)` | Glow effects |
| `--neon` | `#00ffaa` | Text labels |
| `--neon-bright` | `#ccffee` | Active/highlighted text |
| `--green` | `#00ff41` | Success, highlights |
| `--yellow` | `#ffff00` | Nav numbers, warnings |
| `--magenta` | `#ff00ff` | Special accents |
| `--red` | `#ff4444` | Errors |
| `--text` | `#00cc88` | Body text |
| `--muted` | `#006655` | Dimmed text |
| `--border` | `#008888` | All borders |

## Flask API Routes

### Main App
| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serves index.html |
| `/lab` | GET | Serves lab.html (Video Lab) |
| `/static/<file>` | GET | Static assets |
| `/audio/<file>` | GET | TTS audio files |
| `/renders/<file>` | GET | Rendered video files |
| `/api/chat` | POST | SSE streaming chat (TWIN or SHADOW) |
| `/api/boardroom` | POST | SSE multi-agent boardroom session |
| `/api/brainstorm` | POST | SSE brainstorming/podcast session |
| `/api/advisor` | POST | SSE advisor chat (legal or marketing) |
| `/api/podcast` | POST | SSE multi-character podcast/show dialogue |
| `/api/tts` | POST | Multi-voice TTS; returns audio URL |
| `/api/builder` | POST | SSE builder bot (build + architect mode) |
| `/api/builder/deploy` | POST | Loopback-only: write script to disk + register pipeline job |
| `/api/pipeline` | GET/POST | Pipeline job queue |
| `/api/pipeline/<id>` | DELETE | Remove pipeline job |

### Video Lab
| Route | Method | Purpose |
|---|---|---|
| `/api/lab/upload` | POST | Upload video/audio/image to uploads/ |
| `/api/lab/music` | GET | List music tracks in static/music/ |
| `/api/lab/music/add` | POST | Upload new music track |
| `/api/lab/storyboard` | POST | TWIN (Groq) generates JSON storyboard from concept |
| `/api/lab/image` | POST | FAL flux/schnell image generation per scene |
| `/api/lab/video/generate` | POST | Start FAL→Replicate video gen job (background thread) |
| `/api/lab/video/status/<id>` | GET | Poll video job status |
| `/api/lab/voice` | POST | gTTS voiceover MP3 from script |
| `/api/lab/render` | POST (SSE) | FFMPEG assembly: concat clips + mix audio |
| `/api/lab/concept` | GET/POST | Bot→Lab concept bridge |

## Key Rotation

- Groq: `GROQ_API_KEY_1` → `GROQ_API_KEY_2` → `GROQ_API_KEY_3` on 429/auth error
- AIML: `AIML_API_KEY_1` → `AIML_API_KEY_2` → `AIML_API_KEY_3` on 429/auth error

## TTS

- Google Cloud TTS (multi-voice, per-persona) when `GOOGLE_APPLICATION_CREDENTIALS` is set
- gTTS single-voice fallback when Google Cloud is absent
- Voice assignments in `TTS_VOICES` dict in `part1_registry.py`

## Video Lab Pipeline

1. User enters concept → `/api/lab/storyboard` → TWIN generates JSON scenes
2. Click IMAGES → `/api/lab/image` per scene → FAL flux/schnell; reference frame extracted by FFMPEG for visual continuity
3. Click VIDEO → `/api/lab/video/generate` → FAL WAN-2.1 (primary) → Replicate MiniMax (fallback); polled via `/api/lab/video/status/<id>`
4. Click VOICEOVER → `/api/lab/voice` → gTTS MP3 from storyboard voiceover lines
5. Click RENDER → `/api/lab/render` SSE → FFMPEG: slideshow from images + concat clips + mix audio → MP4 in renders/
6. Boards/Builder can push concept JSON to `/api/lab/concept` → auto-fills on /lab page load

## FFMPEG

ffmpeg is available via Nix at `/nix/store/.../bin/ffmpeg`. Operations used:
- `images_to_slideshow()` — concat images as video slideshow
- `concatenate_clips()` — concat MP4 clips
- `mix_audio_onto_video()` — voiceover + background music mix (music at 25% vol)
- `extract_last_frame()` — extract last frame of clip as PNG reference

## Deploy Security

- `/api/builder/deploy` is loopback-only (`127.0.0.1`/`::1`); all browser requests return 403
- Filename validated with `^[a-zA-Z0-9_-]{1,64}$` before writes
- 64KB code size cap
- Deploy logic factored into `_do_deploy_script()` — called by Flask route and `/deploy` Telegram command

## Running

```
cd 'twin shadow flat' && python3 main.py
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TOKEN` | Yes (bot) | Telegram bot token |
| `GROQ_API_KEY_1/2/3` | Yes (TWIN) | Groq key rotation |
| `OPENROUTER_API_KEY` | Yes (board/SHADOW) | OpenRouter key |
| `AIML_API_KEY_1/2/3` | Yes (builder) | AIML key rotation |
| `OLLAMA_API_KEY` | Yes (CAPI) | Ollama Cloud key |
| `GROK_API_KEY` | Shadow Video | Grok/Aurora key |
| `FAL_KEY` | Video Lab | FAL.ai key (image + video gen) |
| `REPLICATE_API_TOKEN` | Video Lab | Replicate key (video fallback) |
| `HUGGINGFACE_API_KEY` | Video Lab | HuggingFace key (video fallback) |
| `GOOGLE_APPLICATION_CREDENTIALS` | TTS | Path to GCP service account JSON |
| `PORT` | No | Web port (default: 5000) |
