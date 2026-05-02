# Twin Shadow

An esoteric/occult comedy content platform: Python Flask web server + Telegram bot with a retro DOS-style UI, multi-model AI hierarchy, boardroom sessions, brainstorming, TTS multi-voice audio, a Builder bot, and a Video Lab.

## Architecture

- **Flask web server** — port 5000, started by `part6_bot.py` at import time in a daemon thread
- **Telegram bot** — runs concurrently via `python-telegram-bot` async loop; requires `TOKEN` env var
- **Web UI** — `static/index.html` — DOS-style retro interface with press-start-2P font, desktop background image, chat panel overlaid on purple box
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
| `part4_video.py` | Video generation (FAL/Replicate/HuggingFace) |
| `part5_orchestrator.py` | Execution engine, vault, RAG, cross-module validation |
| `part6_bot.py` | Flask server + all API routes + Telegram bot handlers |
| `part7_commands.py` | Telegram bot command handlers |
| `part8_personas.py` | System prompts: CAPI, SHADOW, TWIN, board members, brainstorm, builder |
| `static/index.html` | Full DOS-style web UI (chat, boardroom, brainstorm, pipeline, settings) |
| `static/desk_bg.jpg` | Desktop background image |
| `static/mobile_bg.jpg` | Mobile background image |
| `static/send_btn.jpg` | Send button image |
| `audio/` | TTS-rendered MP3 output files |

## Flask API Routes

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serves index.html |
| `/static/<file>` | GET | Static assets |
| `/audio/<file>` | GET | TTS audio files |
| `/api/chat` | POST | SSE streaming chat (TWIN or SHADOW) |
| `/api/boardroom` | POST | SSE multi-agent boardroom session |
| `/api/brainstorm` | POST | SSE brainstorming/podcast session |
| `/api/tts` | POST | Multi-voice TTS; returns audio URL |
| `/api/builder` | POST | SSE builder bot (code generation) |
| `/api/pipeline` | GET/POST | Pipeline job queue |
| `/api/pipeline/<id>` | DELETE | Remove pipeline job |

## Key Rotation

- Groq: `GROQ_API_KEY_1` → `GROQ_API_KEY_2` → `GROQ_API_KEY_3` on 429/auth error
- AIML: `AIML_API_KEY_1` → `AIML_API_KEY_2` → `AIML_API_KEY_3` on 429/auth error

## TTS

- Google Cloud TTS (multi-voice, per-persona) when `GOOGLE_APPLICATION_CREDENTIALS` is set
- gTTS single-voice fallback when Google Cloud is absent
- Voice assignments in `TTS_VOICES` dict in `part1_registry.py`

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
| `GROK_API_KEY` | Video | Grok/Aurora key |
| `FAL_KEY` | Video | FAL.ai key |
| `REPLICATE_API_TOKEN` | Video | Replicate key |
| `HUGGINGFACE_API_KEY` | Video | HuggingFace key |
| `GOOGLE_APPLICATION_CREDENTIALS` | TTS | Path to GCP service account JSON |
| `PORT` | No | Web port (default: 5000) |
