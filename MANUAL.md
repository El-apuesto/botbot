# TWIN SHADOW — Complete System Manual

> **Read this if you want to understand what this system is, how every piece works, and why decisions were made the way they were.**

---

## What This Is

Twin Shadow is an **esoteric/occult comedy content platform** — part web app, part Telegram bot, part autonomous AI engine. It runs a hierarchy of AI models that talk to each other, argue, generate content, write code, produce videos, run background research, and remember what they did.

The UI is styled as a BBS terminal from 1992. The AI behind it is anything but vintage.

---

## How To Run It

```bash
# From workspace root
python3 main.py
```

That single command starts **both** the Flask web server (port 5000) and the Telegram bot simultaneously. They run in the same process — Flask in a daemon thread, the Telegram bot in the main async loop.

**Required environment variables:**

| Variable | What breaks without it |
|---|---|
| `TOKEN` | Telegram bot is disabled; web UI still works |
| `GROQ_API_KEY_1/2/3` | TWIN (primary chat voice) goes silent |
| `OPENROUTER_API_KEY` | Legacy route — most traffic now uses Venice directly |
| `OLLAMA_API_KEY` | CAPI (supreme authority AI) cannot respond |
| `OLLAMA_BASE_URL` | CAPI cannot reach its cloud endpoint |
| `FAL_KEY` | Image generation in Video Lab stops working |
| `REPLICATE_API_TOKEN` | Video generation fallback (FAL → Replicate) breaks |
| `CEREBRAS_API_KEY` | Routing/summarization slows to Groq |

---

## File Map

```
main.py                  ← Entry point. Starts Flask + Telegram bot.
part1_registry.py        ← Every model, every provider, every routing rule.
part2_router.py          ← Sends requests to the right AI with key rotation + fallback.
part3_modules.py         ← Reusable AI task functions (brief, review, code, business).
part4_video.py           ← Video generation pipeline + all FFMPEG operations.
part5_orchestrator.py    ← Execution engine: build → review → approve → vault.
part6_bot.py             ← Flask server + Telegram bot, wired together.
part7_commands.py        ← /boardroom /brainstorm /shadow and all Telegram commands.
part8_personas.py        ← Every AI's personality and system prompt lives here.
part9_web.py             ← Every HTTP route the web UI calls.
part9_supabase.py        ← Optional Supabase cloud storage for uploads + renders.
part10_story.py          ← Story bible builder + chapter-by-chapter novel generation.
part11_book.py           ← Children's book generator (AI story + AI illustrations → PDF).
part12_social.py         ← Social media content pipeline (upload → transcribe → cut clips).
part13_daemon.py         ← Autonomous background task engine with a heartbeat loop.
part14_gorilla.py        ← Guerrilla marketing war room (Moneyball-philosophy committee).
part14_hardening.py      ← Startup health checks for every API provider.
part15_missions.py       ← Background research missions that run without user watching.
part16_committees.py     ← Dynamic committee builder: TWIN assembles custom expert panels.
part_db.py               ← PostgreSQL wrapper (psycopg2) for persistent session storage.
part_llm_config.py       ← Admin panel persistence: role/model/prompt overrides saved to disk.
tsai/static/index.html   ← The entire web UI — all 11 tabs, all JS, all CSS in one file.
tsai/static/lab.html     ← Video Lab page — separate page at /lab.
```

---

## The AI Hierarchy

This is not just "call ChatGPT." There is a deliberate power structure:

```
CAPI  (Ollama Cloud — Qwen3-Coder 480B)
  └─ True supreme authority. Hidden from all other models.
     Even SHADOW does not know CAPI exists above it.
     Used for: CAPI chat seat, moderating boardroom sessions.

SHADOW  (Venice — uncensored model)
  └─ What every other model believes is the top authority.
     TWIN defers to SHADOW silently, never admits this.
     Used for: uncensored chat mode, shadow boardroom, shadow brainstorm.

TWIN  (Groq — Llama 3.3 70B)
  └─ The primary chat voice. Dry wit, deadpan occult comedy.
     Defers to SHADOW. Does NOT reveal the hierarchy to users.
     Used for: HOME chat, storyboards, summarization.

Board Members  (NVIDIA, Venice — various large models)
  └─ Report only to TWIN. Unaware of CAPI entirely.
     Strategy / Finance / Creative / Tech / Ops / Distribution.
     Each seat has a name, alias, and role persona.
```

**Why this structure?** When you run a boardroom session, CAPI acts as the moderator but the board members don't know they're being moderated by a hidden authority. It creates authentic-feeling disagreement without the models second-guessing who's in charge.

---

## Part 1 — Registry (`part1_registry.py`)

**What it does:** This is the single source of truth for every model name, every API provider, and every routing rule in the entire system. No other file hardcodes a model name — they all go through this.

**Key structures:**

**`PROVIDERS`** — Every API provider configured in one place:
- `ollama_cloud` — CAPI's private endpoint (Qwen3-Coder 480B)
- `ollama_local` — Local Dolphin models (desktop only, not available remotely)
- `cerebras` — Ultra-fast Llama 8B + Qwen for routing/summarization
- `nvidia` — Free-tier models: MiniMax, GLM5, Mistral, Codestral, Nemotron, etc.
- `venice` — Uncensored models: Venice 1.2, Gemma4, Hermes 405B, Qwen Coder
- `groq` — TWIN's provider: Llama 3.3 70B, QwQ 32B, Whisper V3 for transcription
- `grok` — Grok/Aurora for Shadow video generation
- `fal` — Image gen (Flux Schnell) + video gen (WAN 2.1)
- `replicate` — MiniMax video fallback when FAL is unavailable

**`TASK_MODELS`** — Maps every task type to a `(provider, model_key)` pair. Examples:
- `"twin"` → `("groq", "llama")` — TWIN uses Groq Llama
- `"builder"` → `("venice", "qwen_coder_480_turbo")` — Builder uses Qwen Coder
- `"board_strategy"` → `("nvidia", "minimax_m25")` — Strategy seat uses MiniMax
- `"committee_strategy_1"` → `("nvidia", "nemotron_ultra_253b")` — Committee uses Nemotron

**`TOKEN_LIMITS`** — Hard caps per role category. Board members get 400 tokens per turn to prevent one model from dominating. Hermes gets a 30-token cap in boardroom specifically because it tends to monologue.

**`BOARD_MEMBERS` / `SHADOW_BOARD_MEMBERS`** — The 9 regular boardroom seats and 5 shadow boardroom seats. Each entry has a `key` (routes to the right model), `name` (display), `alias` (personality name), and `role`.

**`BRAINSTORM_MEMBERS`** — 9 simultaneous voices for brainstorm sessions. All run in parallel, TWIN synthesizes.

**`COMMITTEE_STRUCTURE`** — Each of the 6 main board seats has 3 sub-models that run a pre-discussion before the main seat speaks. So "Strategy" is actually 3 models arguing internally, then producing a consolidated position.

---

## Part 2 — Router (`part2_router.py`)

**What it does:** Every AI call in the system goes through here. It handles key rotation, provider fallback, and the async/sync bridge.

**Key functions:**

**`stream_task(task_type, messages, max_tokens)`**
The main streaming function. Given a task type like `"twin"` or `"builder"`, it:
1. Checks offline mode — if enabled, routes everything to local Ollama
2. Checks `part_llm_config` for admin panel overrides (can redirect any task to any model)
3. Looks up the provider and model from `TASK_MODELS` in Part 1
4. Streams the response as an async generator of string chunks

**`call_task(task_type, messages)`**
Non-streaming version — returns the full response as a single string. Used when you need the complete output before proceeding (e.g., generating a JSON brief).

**Key rotation** — Groq and AIML each have 3 API keys (`_1`, `_2`, `_3`). When a key hits a 429 rate limit or auth error, the router automatically tries the next key in the list. The Unicode bug fix is important: Replit sometimes stores env vars with non-ASCII characters; those keys are silently skipped with an ASCII-safety check.

**Provider fallback** — If all keys for Groq fail with server errors (5xx), the router falls back to Cerebras automatically. Same for NVIDIA → Cerebras. Venice has no fallback because uncensored content can't be safely routed to censored models.

**`_PROVIDER_FALLBACK`** — The fallback map is explicit in the code so you can see exactly what falls back to what:
```python
"groq":   ("cerebras", "llama_small")  # Groq 500/509 → Cerebras
"nvidia": ("cerebras", "llama_small")  # NVIDIA 410/503 → Cerebras
```

---

## Part 3 — Modules (`part3_modules.py`)

**What it does:** Reusable AI task functions that the orchestrator and web routes call. Each function handles one specific type of AI work.

**`generate_brief(idea)`** — Takes a raw idea string, sends it to the BRIEF model (Groq, fast), gets back a structured JSON brief with summary, goals, project type, effort estimate, modules needed, and tone. Has retry logic: if the model returns malformed JSON, it re-prompts up to 2 times.

**`evaluate(responses)`** — Quality control: given multiple AI responses, picks the best one. Current implementation: longest non-empty response wins. Simple but effective.

**`code_review(code_text)`** — Sends code to a review model, gets back `{"approved": bool, "issues": [...], "summary": "..."}`. The `validate_review_result()` helper strips markdown fences and handles malformed JSON so the caller always gets a clean dict.

**`run_creative(brief, style)`** — Creative content generation. Takes a project brief and style direction, returns creative output.

**`run_code(brief)`** — Code generation module. Different from the builder — this is for quick code generation as part of a larger pipeline.

**`run_business(brief)`** — Business analysis and planning. Routes through Venice Hermes 405B.

**`shadow_fallback(brief)`** — When other modules fail or the request needs to go uncensored, this routes to SHADOW.

**`shadow_audit(content)`** — SHADOW reviews content for quality/appropriateness from an uncensored perspective.

---

## Part 4 — Video (`part4_video.py`)

**What it does:** Everything involving video files — generation, assembly, and FFMPEG utilities.

**Video generation chain:**
1. **FAL WAN 2.1** (primary) — Image-to-video using FAL.ai
2. **Replicate MiniMax** (fallback) — If FAL fails
3. **HuggingFace** (second fallback) — If Replicate fails

**Shadow Video** uses Grok's Aurora model for image generation, a completely separate path from the lab video pipeline.

**FFMPEG utilities — all the actual operations:**

`extract_last_frame(video_path)` — Extracts the final frame of a video clip as a PNG. Used for visual continuity: the last frame of clip N becomes the reference image for clip N+1, so scenes flow together instead of jumping.

`images_to_slideshow(image_paths, output_path, fps)` — Converts a list of still images into a video slideshow. Used when you have FAL-generated images but no video clips yet.

`concatenate_clips(clip_paths, output_path)` — Joins multiple MP4 clips into one file using FFMPEG concat demuxer.

`mix_audio_onto_video(video_path, voiceover_path, music_path, output_path)` — Combines video + voiceover + background music. Music is mixed at 25% volume so voiceover stays intelligible.

`download_file(url, dest_path)` — Downloads a URL to disk with redirect following. Used to pull FAL/Replicate output URLs to the local filesystem before FFMPEG processing.

**FFMPEG location:** Not assumed to be in PATH. The code finds it via `shutil.which("ffmpeg")` first, then falls back to a hardcoded Nix store path. This is because in Replit NixOS, FFMPEG is installed by Nix but may not be on the PATH depending on shell context.

---

## Part 5 — Orchestrator (`part5_orchestrator.py`)

**What it does:** The execution engine that coordinates multi-step AI pipelines. Build a brief → generate content → review it → approve → store in vault.

**`Orchestrator` class** — The main class. Has methods:
- `build(idea)` — Takes a raw idea, generates a structured brief, returns a project ID
- `approve(project_id)` — Runs the full pipeline: creative → code → business → video as needed, with cross-validation between modules
- `enqueue_approve(project_id)` — Queues approval for background execution (requires Redis/Arq)

**The Vault** — In-process storage for completed work:
- `vault_list()` — All stored results
- `vault_get(id)` — Retrieve a specific result
- `vault_last()` — Most recent result
- `vault_undo()` — Remove the most recent result
- `vault_clear()` — Wipe everything

The vault is **in-memory only** — it survives the session but not a process restart. For persistence, the web routes save to PostgreSQL via `part_db.py`.

**Event system** — `emit_event()` + `EventType` enum track what's happening: task started, task completed, build started, boardroom opened, audit passed, etc. Currently no-op stubs (the runtime module that consumed them was removed), but the hooks are in place for future monitoring.

**Cross-validation** — After each module runs, its output is sent to another model for review. A creative output gets code-reviewed by a different model. This prevents single-model hallucinations from making it to the vault unchecked.

---

## Part 6 — Bot (`part6_bot.py`)

**What it does:** The integration layer. Wires Flask and the Telegram bot into the same running process.

**How Flask + Telegram coexist:** Flask is started in a daemon thread on port 5000. The Telegram bot runs in the main asyncio event loop. Both start at import time when `part6_bot.py` is imported by `main.py`.

**Deployment detection:** The code checks `REPLIT_DEPLOYMENT` to determine which Telegram token to use:
- **Deployed (production):** Uses `TOKEN` — the real bot
- **Development:** Uses `CHILD_BOT_TOKEN` — a separate test bot, so dev polling doesn't conflict with the production bot running simultaneously

**Telegram handlers registered here:**
- `/model_set`, `/model_traits`, `/model_end` — persona management (from Part 7)
- `/boardroom`, `/brainstorm`, `/shadow` — session commands (from Part 7)
- `/boss_voice`, `/end`, `/extend` — session control (from Part 7)
- `/daemon`, `/task`, `/queue`, `/clear_done` — autonomous daemon (from Part 13)
- `/deploy` — deploy a built script to disk (loopback-only security)
- Plain messages → `handle_user_turn()` — routes to TWIN or active session

---

## Part 7 — Commands (`part7_commands.py`)

**What it does:** The actual logic for every Telegram command that involves AI sessions.

**`/boardroom <rounds> <topic>`**
Starts a structured multi-round debate. Each board member speaks in sequence, with a token cap to keep turns tight. CAPI moderates. After all rounds, TWIN produces a synthesis. The Hermes 30-token cap prevents monologuing — at 30 tokens, every response is a punchy one-liner or it gets cut off.

**`/brainstorm [podcast] <topic>`**
All 9 brainstorm voices run in parallel (asyncio.gather), then TWIN synthesizes the results. The `podcast` flag changes the synthesis format to a dialogue script instead of a bullet summary.

**`/shadow <command> <args>`**
Same as the equivalent command but uses SHADOW_BOARD_MEMBERS and SHADOW_BRAINSTORM_MEMBERS — the fully uncensored rosters.

**`/model_set <key> <description>`**
Assigns a custom personality/traits to a specific model seat. Stored in-session. The key maps to the board member's task routing key (e.g., `board_strategy`).

**`/boss_voice <message>`**
Queues a message from the user to be injected as the next round's opening. Lets you redirect a session mid-run without stopping it.

**`/extend <rounds>`**
Adds rounds to an active boardroom or brainstorm without restarting. The session continues from where it stopped.

---

## Part 8 — Personas (`part8_personas.py`)

**What it does:** Every AI character's personality is a system prompt. This file is where they all live.

**`TWIN_SYSTEM`** — Dry, deadpan, sharp. Knows the full platform and guides users to the right tool. Defers to SHADOW silently, never mentions the hierarchy. Occult comedy tone without forcing it.

**`SHADOW_SYSTEM`** — Uncensored authority. Darker, more direct. What other models believe is the top of the hierarchy.

**`CAPI_SYSTEM`** — Supreme authority. Hidden from other models. Knows it sits above SHADOW. Used as boardroom moderator under a different cover identity.

**`BUILDER_SYSTEM`** — Focused entirely on code generation. No personality fluff — just build what's asked, output clean code with proper formatting.

**`BUILDER_UNCENSORED_SYSTEM`** — Same as BUILDER but with Venice uncensored model. Used when the build request involves content that censored models refuse.

**`board_member_system(member, topic, history)`** — Dynamically generates a system prompt for a specific boardroom seat given the current topic and conversation history. Each member gets their role, their name, and the context of what's been discussed.

**`brainstorm_system(member, topic)`** — Same for brainstorm sessions. Shorter, more freeform than boardroom.

**`get_system_prompt(task_key)`** — The lookup function every route calls. Checks `part_llm_config` for admin overrides first. If an override exists, returns the custom prompt. Otherwise returns the hardcoded default from the `_MAP` dict.

**Admin override check** — This is how the LLM Admin panel in Settings takes effect. If you edit the TWIN prompt in Settings → LLM Admin → PROMPTS and save, `get_system_prompt("twin")` returns your custom version on all subsequent requests.

---

## Part 9 — Web Routes (`part9_web.py`)

**What it does:** Every HTTP endpoint the web UI and Telegram bot call. Registered into Flask via `register_routes(app)`.

**Auth system:**
- First account created automatically becomes admin
- Login via username/password stored in `users.json` (bcrypt-hashed)
- Invite-only registration: `/register?code=XXXX` or paste code into the visible field
- `WEB_PASSWORD` env var bypasses invite system and auto-grants admin
- Session cookie for state; `@_login_required` decorator on every non-public route

**Streaming — how SSE works here:** Flask is synchronous, but the AI calls are async. The bridge: each streaming request spawns a daemon thread that runs an asyncio event loop, streams chunks into a `queue.Queue`, and the Flask response generator pulls from that queue and sends SSE events. This avoids blocking the Flask worker while the LLM streams.

**Core chat routes:**
- `POST /api/chat` — TWIN or SHADOW streaming chat. Takes `{message, bot, history, system_prompt}`
- `POST /api/boardroom` — Multi-agent boardroom session with SSE streaming
- `POST /api/brainstorm` — Brainstorm session, all voices simultaneously
- `POST /api/advisor` — Legal or marketing advisor chat
- `POST /api/podcast` — Multi-character podcast/show dialogue

**Builder routes (background job system):**
- `POST /api/builder/job` — Creates a background build job, returns `{job_id}` immediately
- `GET /api/builder/job/<id>` — Poll job status: `{status, chunks, error, output_incomplete}`
- `POST /api/builder/job/<id>/continue` — Append a continuation request to an existing job
- `GET /api/builder/builds` — List all saved builds (newest first)
- `GET /api/builder/builds/<filename>` — Get full output of a completed build
- `DELETE /api/builder/builds/<filename>` — Delete a build from disk

**Why background jobs for builder?** The AI code generation can take 60-90 seconds. If the user navigates away or the request times out, a synchronous handler would lose the output. The background job system writes progress to memory (the `_builder_jobs` dict) and saves the final result to disk (`builds/*.json`). The frontend polls every 2.5 seconds and resumes if the user returns to the Code Lab tab.

**Auto-continue logic:** When a build job finishes, the router checks if the output looks incomplete — unclosed code fences, output that ends with `...` or `[continues`, mid-function cutoffs. If incomplete, it sets `output_incomplete: True` in the status. The frontend auto-sends up to 3 continuation requests before showing a manual `[ CONTINUE ]` button.

**Deploy security** (`POST /api/builder/deploy`):
- Loopback-only: requests from any IP other than `127.0.0.1` / `::1` get a 403
- Filename validated against `^[a-zA-Z0-9_-]{1,64}$` — no path traversal
- 64KB code size cap
- The builder bot calls this endpoint from itself; browser requests can never reach it

**Video Lab routes:**
- `POST /api/lab/storyboard` — TWIN generates a JSON scene list from a concept
- `POST /api/lab/image` — FAL Flux Schnell generates an image for a single scene
- `POST /api/lab/video/generate` — Starts FAL video gen job, returns immediately
- `GET /api/lab/video/status/<id>` — Poll video job progress
- `POST /api/lab/voice` — gTTS voiceover MP3 from a script
- `POST /api/lab/render` (SSE) — FFMPEG assembly: slideshow + clips + audio → MP4

**Admin panel routes:**
- `GET /api/admin/config` — All roles with current assignments, override status, prompts
- `GET /api/admin/models` — All providers with all available model keys
- `POST /api/admin/role/<role>` — Override a role's provider + model
- `POST /api/admin/prompt/<role>` — Override a role's system prompt
- `POST /api/admin/reset/role/<role>` — Remove model override, return to default
- `POST /api/admin/reset/prompt/<role>` — Remove prompt override, return to default
- `GET /api/admin/ollama/models` — Discover locally running Ollama models
- `POST /api/admin/ollama/assign` — Add a discovered Ollama model to the registry and assign to a role
- `POST /api/admin/config/template` — Save current config state as a named snapshot
- `POST /api/admin/config/load/<name>` — Restore a saved config snapshot

---

## Part 9 Supabase (`part9_supabase.py`)

**What it does:** Optional cloud storage for lab uploads and renders. When configured, files go to Supabase Storage instead of (or in addition to) local disk.

**Bucket:** `twin-shadow-lab` with two folders: `uploads/` and `renders/`

**Two database tables:**
- `lab_uploads` — Tracks every uploaded file: name, type, size, duration, storage path, public URL
- `lab_renders` — Tracks every rendered MP4: name, size, storage path, public URL

**When it's active vs inactive:** The module checks for `SUPABASE_URL` and `SUPABASE_KEY` env vars. If absent, the web routes fall back to local file serving from `uploads/` and `renders/` directories. The rest of the app works fine without Supabase — it's an enhancement, not a requirement.

---

## Part 10 — Story (`part10_story.py`)

**What it does:** Two separate things under one roof: a story bible builder and a chapter-by-chapter novel generator.

**Story Bible Builder** — Conversational: you talk to TWIN, it asks one question at a time about your world, characters, tone, conflict. When it has enough to work with, it outputs a structured JSON bible. The bible is injected into every subsequent chapter prompt so the AI never forgets the world rules or character voices, even 20 chapters in.

**Chapter Generator** — Rolling 512-word context window. The last 512 words of the previous chapter are always in the next prompt, providing continuity without blowing up the context window with 40,000 words of prior text.

**Key design decision:** The bible + rolling context combination solves the long-form coherence problem. Without the bible, AI-generated novels forget character names by chapter 3. Without the rolling window, they forget what happened in the last scene.

---

## Part 11 — Book (`part11_book.py`)

**What it does:** Generates illustrated children's books as print-ready PDFs.

**Two layout templates:**
1. **Classic** — Text above or below the illustration (stacked)
2. **Immersive** — Full-bleed AI illustration with a white text box overlaid

**Four paper/fold options** — The PDF is designed for folded-booklet printing:
- Letter (8.5×11 → 5.5×8.5 booklet), Tabloid (11×17 → 8.5×11), A5, A4

**What it outputs:** Two PDFs — the book itself (one page per leaf) plus a separate instructions PDF with print guide and supply list for whoever prints it.

**Image generation:** FAL Flux Schnell per page. The AI writes the story first, then each page's illustration prompt is generated from the page text.

---

## Part 12 — Social (`part12_social.py`)

**What it does:** Full social media content pipeline starting from raw audio or video.

**The pipeline in order:**
1. Upload a recording (audio or video file)
2. FFMPEG rips audio from video if needed
3. Groq Whisper transcribes the audio (raw, cleaned, or summarized mode)
4. TWIN identifies the best short clips with timestamps ("2:14-2:47 — killer point about X")
5. FFMPEG cuts those clips from the original
6. Platform optimizer formats each clip for TikTok, Instagram, Facebook, or YouTube (different aspect ratios, captions styles, etc.)
7. Generates description, hashtags, and a YouTube title for each clip
8. Thumbnail: either a screenshot at a specific timestamp with text overlay, or an AI image prompt
9. Handoff: sends clip data to the Video Lab for further processing

**Why Groq Whisper?** Fastest transcription available. A 60-minute recording comes back in under 30 seconds. The transcription is what the clip-identification model reads, so speed matters.

---

## Part 13 — Daemon (`part13_daemon.py`)

**What it does:** A self-driving task engine that runs on a heartbeat without human input.

**How it works:**
- SQLite database (`daemon_tasks.db`) stores a task queue
- Every `DAEMON_INTERVAL` seconds (default 300 = 5 minutes), the daemon wakes, pulls the highest-priority pending task, runs it through the AI, and stores the result
- After each task completes, the AI is asked to generate follow-up tasks, which are added to the queue automatically
- Results are reported to a designated Telegram chat

**Telegram commands:**
- `/daemon setchat` — Register the current chat as the daemon's report channel
- `/daemon start` / `/daemon stop` — Start/stop the heartbeat
- `/task <description>` — Add a task to the queue with default priority 5
- `/task <priority> <description>` — Add with explicit priority (lower number = higher priority)
- `/queue` — Show the current task queue
- `/clear_done` — Remove completed tasks from the queue

**The self-generating loop:** The daemon is designed to keep itself busy. Give it an initial research task and it will split it into sub-tasks, execute them, combine results, identify gaps, and generate new tasks to fill those gaps. It stops only when the queue empties or you tell it to.

---

## Part 14 — Guerrilla (`part14_gorilla.py`)

**What it does:** A specialized 6-specialist committee for guerrilla marketing strategy, built on the Moneyball philosophy.

**Moneyball philosophy in this context:** Find the inefficiencies in your market that everyone ignores. Maximize expected value per dollar, not maximum upside. Small asymmetric bets that scale. Don't compete where everyone else is competing.

**TWIN runs first** — Before the committee convenes, TWIN generates a pre-brief that identifies:
- The obvious playbook and why it will fail (what the category always does)
- The Moneyball signal (what actually predicts success but nobody measures)
- The competitive blindspot (what competitors aren't doing)
- The audience truth (who the actual buyer is vs who the brief assumes)
- Budget reality check

**Then the 6 specialists run:**
1. **Scout** — Market intelligence, audience targeting
2. **Street Intel** — Ground-level tactics, community infiltration
3. **Culture** — Cultural moment identification, timing
4. **Arbitrage** — Platform and channel inefficiencies to exploit
5. **Narrative** — Story framing, press angles, viral mechanics
6. **Closer** — Conversion optimization, call-to-action design

TWIN synthesizes their outputs into a final blueprint.

---

## Part 14 Hardening (`part14_hardening.py`)

**What it does:** Verifies every API provider is reachable at startup and every N hours. Tells you what's broken before your users hit errors.

**How it checks:**
- OpenAI-compatible providers: sends a minimal chat completion request (1 token max)
- Grok: checks key presence only (Aurora image gen doesn't have a chat endpoint to ping)
- FAL: imports the SDK and checks for the key
- Non-chat models (NVIDIA Whisper, embeddings, etc.): skipped because they'd fail a chat ping for the wrong reasons

**`get_status()`** — Returns a dict of every provider's last check result: online/offline, timestamp, error message if any.

**`verify_all_endpoints()`** — Runs all checks concurrently. Called at startup and then on the heartbeat.

This is why the Settings panel in the UI can show a live health status for every provider.

---

## Part 15 — Missions (`part15_missions.py`)

**What it does:** Autonomous background research missions. You launch a mission, close the browser, come back in 10 minutes, and the results are there.

**Six mission types:**
1. **Gorilla Marketing Blueprint** — 6-specialist committee (from Part 14) + TWIN synthesis
2. **Full Boardroom Analysis** — Complete 9-seat board + TWIN executive summary
3. **Strategy Deep Dive** — 3 strategy models debate, TWIN decides
4. **Creative Brief** — Creative committee generates concepts, copy angles, visual direction
5. **Competitive Intelligence** — Multi-model competitive analysis: threats, gaps, opportunities
6. **Custom Committee** — You describe what expertise you need in plain English, TWIN assembles the right models

**Mission lifecycle:** `pending → running → done` (or `error`). Each phase's output is stored in `results[phase_name]` so you can see each model's contribution separately.

**Storage:** Missions saved to `missions/*.json` on disk — they survive server restarts. The web UI polls the mission status endpoint until `done` or `error`.

**Why missions instead of just waiting?** Long sessions (full boardroom with committee pre-discussions) take 5-8 minutes. Making users sit and watch a loading spinner for 8 minutes is bad UX. Missions run in background threads, store results to disk, and the user just checks back.

---

## Part 16 — Committees (`part16_committees.py`)

**What it does:** Dynamic committee assembly. Instead of picking a preset mission type, you describe what expertise you need in plain English and TWIN picks the right models.

**The model roster** — 19 specialists across 6 domains, each with a description of their actual capabilities:
- Strategy: Nemotron (macro strategy), Qwen-Strat (competitive frameworks), DeepSeek (chain-of-thought, challenges assumptions)
- Finance: Palmyra-Fin (financial modeling), Magistral (risk/compliance), Mistral-Fin (pricing/margins)
- Creative: Palmyra-C (brand/visual direction), Qwen-C (copywriting/taglines), GLM (experimental/storytelling)
- Tech: Codestral (code architecture), Granite (infrastructure/cloud), GPT-Tech (product/integrations)
- Ops: Seed (operational planning), Stepfun (rapid execution), Qwen3 (process optimization)
- Distribution: GPT-OSS-120B (scale/channels), Qwen-35 (audience growth), DeepSeek-Dist (market entry)

**How TWIN assembles a committee:** The `COMMITTEE_ROSTER` list (with all descriptions) is sent to TWIN along with the user's requirement. TWIN outputs a JSON array of 3-6 model keys best suited to the task. Those models are instantiated as a committee and run in parallel.

**Saved committees** — Committees you define can be saved to `committees/*.json` and reused in future missions without re-specifying the requirements.

---

## Part DB (`part_db.py`)

**What it does:** Thin PostgreSQL wrapper. All Flask routes that need to persist data across sessions or server restarts use this.

**What it stores:**
- Chat history per user (indexed by username + bot name)
- Boardroom and brainstorm transcripts
- Builder session outputs
- Pipeline job queue

**Connection handling:** Every operation opens a connection, runs the query, commits, and closes. No connection pool — fine for this workload. `DATABASE_URL` from environment.

**Graceful degradation:** If `DATABASE_URL` is not set or psycopg2 isn't installed, `_AVAILABLE = False` and the module raises `RuntimeError` on use. The web routes check availability and fall back to in-session storage (lost on refresh) if the DB is unavailable.

---

## Part LLM Config (`part_llm_config.py`)

**What it does:** Persistent override layer for the LLM Admin panel. Changes you make in Settings → LLM Admin survive server restarts.

**What it persists (`llm_config.json`):**
- `role_overrides` — `{role: {provider, model_key}}` — redirects any task to any model
- `prompt_overrides` — `{role: "custom system prompt text"}` — replaces any persona's default prompt
- `templates` — Named snapshots of the entire override state
- `ollama_base_url` — Where to find your local Ollama instance
- `offline_mode` — When true, all traffic routes to local Ollama regardless of task type

**Thread safety:** All reads and writes use a `threading.Lock`. Flask handles multiple requests concurrently; without the lock, concurrent admin saves could corrupt the JSON file.

**How overrides flow through the system:**
1. Admin panel saves an override via `set_role("twin", "venice", "llama_70b")`
2. That's written to `llm_config.json`
3. On the next chat request, `stream_task("twin", ...)` calls `get_role_override("twin")`
4. Override found → uses Venice Llama 70B instead of Groq Llama
5. TWIN's response now comes from a different model with no other code changes

---

## The Web UI (`tsai/static/index.html`)

**One file.** The entire web UI — all 11 tabs, all JavaScript, all CSS — is a single HTML file. This is intentional: no build step, no npm, no bundler. The file is served directly and loads instantly.

**11 tabs:**

| # | Tab | What it does |
|---|---|---|
| 1 | HOME | TWIN/SHADOW chat. The `[DASH]` button opens the dashboard overlay. |
| 2 | COMMITTEES | MISSIONS launcher + COMMITTEE builder + GUERRILLA war room. Three sub-tabs. |
| 3 | BOARDROOM | Start a live boardroom session with the full 9-seat board. |
| 4 | VIDEO LAB | Links to `/lab` — the full video pipeline page. |
| 5 | WRITE LAB | Story bible, document reader/analyzer, and novel chapter writer. |
| 6 | CODE LAB | Builder bot (BUILD mode) and Architect mode. |
| 7 | SOCIAL | Social media content pipeline from audio/video uploads. |
| 8 | PODCASTS | Multi-character podcast script generator + CHARACTERS manager. |
| 9 | BRAINSTORM | 9-voice parallel brainstorm with TWIN synthesis. |
| 10 | LEGAL/MKT | Legal advisor (Hermes 405B) and Marketing advisor (DeepSeek Flash) chat. |
| 11 | SETTINGS | User preferences, API settings, LLM Admin panel. |

**The `[DASH]` overlay** — Opens over the HOME tab. Shows:
- Live stats gauges (sessions, boardroom runs, brainstorm runs, renders — fetched from the server)
- Recent activity feed
- **RECENT BUILDS** — Last 8 builder jobs. Click VIEW to open any build in Code Lab.
- **BUILD QUEUE** — Manual job queue you can build and run in sequence.

**Streaming in the UI** — All streaming responses use the EventSource-compatible pattern: `fetch()` with a `ReadableStream` reader, parsing SSE `data:` lines as they arrive. This is used for chat, boardroom, brainstorm, builder, and the FFMPEG render. The user sees text appearing word by word.

**TWIN/SHADOW orchestration buttons** — After every chat response, the UI analyzes the message content and shows context-aware action buttons:
- Response contains code → `[ → CODE LAB ]` (pre-fills builder with your original message)
- Response mentions strategy/analysis → `[ → BOARDROOM ]`
- Response mentions brainstorm/ideas → `[ → BRAINSTORM ]`
- Response mentions video/film → `[ → VIDEO LAB ]`
- Response mentions podcast/episode → `[ → PODCASTS ]`

These aren't magic routing — they're smart navigation shortcuts. Your message is pre-filled in the target panel so you're never starting from scratch.

**Builder auto-continue** — When a build job finishes, the output is inspected for incompleteness (unclosed code fences, ends mid-function, trailing `...`). If incomplete, the UI automatically sends up to 3 continuation requests. After 3 attempts, it shows a manual `[ CONTINUE ]` button.

**LLM Admin panel (Settings tab)** — Collapsible section at the bottom of Settings. Four sub-tabs:
- **ROLES** — Every task role listed with current provider/model. Orange = overridden from default. Provider select + model select per row. SAVE applies immediately. RST reverts to hardcoded default.
- **PROMPTS** — Editable textarea for every role's system prompt. SAVE overrides for all future requests. RESET returns to hardcoded default (double confirmation).
- **OLLAMA** — Set the Ollama base URL. DISCOVER MODELS button pings `localhost:11434/api/tags` and returns all locally running models. Assign any discovered model to any role.
- **TEMPLATES** — Save the current state of all overrides as a named snapshot. Load or delete templates.

---

## Video Lab (`tsai/static/lab.html`)

**Separate page** at `/lab`. The same BBS aesthetic as the main UI.

**The full pipeline:**
1. **STORYBOARD** — Type a concept. TWIN generates a JSON scene list with a description, voiceover line, and mood for each scene.
2. **IMAGES** — Click per scene. FAL Flux Schnell generates an image matching the scene description. The last frame of each image is extracted for visual continuity reference.
3. **VIDEO** — FAL WAN 2.1 generates a video clip from each scene image. Jobs are background threads; the UI polls status every few seconds.
4. **VOICEOVER** — gTTS generates an MP3 from the storyboard's voiceover lines concatenated.
5. **RENDER** — FFMPEG: slideshow from images → concat video clips → mix voiceover + background music → final MP4 in `renders/`.

**Music library** — `static/music/` holds MP3/WAV/OGG tracks. The render pipeline picks a track and mixes it under the voiceover at 25% volume. You can add tracks through the UI.

**Bot→Lab concept bridge** — When a TWIN chat response includes a video concept, the builder can push it to `/api/lab/concept`. When you navigate to `/lab`, that concept auto-fills the storyboard input so you can immediately click GENERATE.

---

## Security Notes

- All routes behind `@_login_required` — unauthenticated users get a redirect to `/login`
- Invite codes required for registration — prevents open signup
- `/api/builder/deploy` is loopback-only (127.0.0.1 only) — browser requests return 403
- Filename sanitization (`^[a-zA-Z0-9_-]{1,64}$`) on all file writes from user input
- 64KB cap on code submitted to deploy endpoint
- WEB_PASSWORD env var for emergency access (bypasses invite, grants admin)
- API keys never appear in responses or logs

---

## Common Failure Modes

| Symptom | Cause | Fix |
|---|---|---|
| TWIN not responding | GROQ keys exhausted (rate limit) | Add GROQ_API_KEY_2 and GROQ_API_KEY_3 |
| Boardroom members all say same thing | Venice API down | Check provider status in Settings → LLM Admin |
| Builder jobs stay at "running" forever | Venice/Groq timeout | Check logs; the job saves partial output to disk |
| TTS returns no audio | gTTS network error (Google API) | Retry; gTTS depends on external Google endpoint |
| Video gen stuck | FAL rate limit | Job auto-retries; check FAL dashboard |
| Register page shows "invite required" with no field | Old register.html being served | Hard refresh (Ctrl+Shift+R) to clear cache |
| Dashboard shows 0 builds | `builds/` directory empty | Run a builder job first |

---

## Directory Structure (runtime files)

```
audio/          ← gTTS + voiceover MP3 files
renders/        ← Final assembled MP4 videos
uploads/        ← Lab uploads (video, audio, images)
builds/         ← Saved builder job outputs (JSON)
missions/       ← Saved mission results (JSON)
committees/     ← Saved committee configurations (JSON)
settings/       ← Per-user settings (JSON per user)
transcripts/    ← Saved session transcripts
daemon_tasks.db ← SQLite database for daemon task queue
users.json      ← User accounts (bcrypt-hashed passwords)
invites.json    ← Active invite codes
llm_config.json ← Admin panel overrides (created on first save)
lab_concept.json← Bot→Lab concept bridge (overwritten each use)
```
