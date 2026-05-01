# Twin Shadow

A Python-based Telegram bot with a Flask web UI, AI integrations, and text-to-speech support.

## Architecture

- **Flask web server** — runs on port 5000, started by `part6_bot.py` at import time
- **Telegram bot** — requires the `TOKEN` environment variable to activate
- **AI integrations** — OpenAI, Replicate, fal-client, HuggingFace, Supabase

## Project Structure

All source files are inside the `twin shadow flat/` directory:

| File | Purpose |
|---|---|
| `main.py` | Entry point — starts Flask + bot |
| `part1_registry.py` | Model registry |
| `part2_router.py` | Task routing / streaming |
| `part3_modules.py` | AI modules |
| `part4_video.py` | Video generation |
| `part5_orchestrator.py` | Orchestrator + vault |
| `part6_bot.py` | Telegram bot + Flask web server |
| `part7_commands.py` | Bot command handlers |
| `part9_hardening.py` | Security hardening |
| `requirements.txt` | Python dependencies |

## Running

The app is started via the `Start application` workflow:
```
cd 'twin shadow flat' && python3 main.py
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TOKEN` | Yes (for bot) | Telegram bot token |
| `PORT` | No | Web server port (default: 5000) |
| `RATE_LIMIT_SECONDS` | No | Rate limit in seconds (default: 10) |
| `MAX_MEMORY` | No | Max memory entries per user (default: 10) |
| `ALLOWED_IDS` | No | Comma-separated Telegram user IDs (empty = all allowed) |

## Docker

A `Dockerfile` is provided at the project root for containerized deployment:
```
docker build -t twin-shadow .
docker run -e TOKEN=your_bot_token -p 5000:5000 twin-shadow
```

## Dependencies

Installed via Python package manager:
- `python-telegram-bot==20.7`
- `openai`, `supabase`, `flask`, `fal-client`
- `replicate`, `huggingface_hub`, `python-dotenv`
- `requests`, `gtts`
