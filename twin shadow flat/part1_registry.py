"""
Twin Shadow — Part 1: Model Registry
All providers, models, task routing, TTS voice assignments.
"""

# ── TTS Voice assignments per persona ──────────────────────────────────────────
# Used by /api/tts for multi-voice boardroom / brainstorm / podcast audio.
# Overridable in Settings via localStorage (sent as JSON in the TTS request).
TTS_VOICES: dict[str, str] = {
    "TWIN":        "en-US-Neural2-D",
    "SHADOW":      "en-US-Neural2-A",   # authoritarian real Shadow (Venice 1.2)
    "GEMMA":       "en-US-Wavenet-C",   # uncensored chat Shadow persona (Gemma 4)
    "ORACLE":      "en-US-Neural2-E",   # Venice 1.1 — shadow boardroom strategist
    "CAPI":        "en-US-Wavenet-B",
    "HERMES":      "en-US-Neural2-J",
    "GPTOSS":      "en-US-Neural2-G",
    "QWEN":        "en-US-Wavenet-D",
    "MISTRAL":     "en-US-Neural2-F",
    "MINIMAX":     "en-US-Wavenet-A",
    "BUILDER":     "en-US-Neural2-I",
    "NARRATOR":    "en-US-Neural2-C",
}

PROVIDERS: dict = {

    "ollama_cloud": {
        "role": "capi",
        "desc": "CAPI. The real top authority. Known only to TWIN and SHADOW.",
        "base_url_env":  "OLLAMA_BASE_URL",
        "api_key_env":   "OLLAMA_API_KEY",
        "default_key":   "ollama",
        "openai_compat": True,
        "models": {
            "qwen": "qwen3-coder:480b",
        },
    },

    "ollama_local": {
        "role": "local_shadow",
        "desc": "Dolphin local. Shadow board seat. Desktop only.",
        "base_url":      "http://localhost:11434/v1",
        "api_key":       "ollama",
        "openai_compat": True,
        "models": {
            "dolphin":        "dolphin-mixtral",
            "dolphin_venice": "dolphin-mistral-24b-venice-edition",
        },
    },

    "cerebras": {
        "role": "specialist",
        "desc": "Cerebras ultra-fast inference. Llama, Qwen, GPT-OSS at high TPS.",
        "base_url":       "https://api.cerebras.ai/v1",
        "api_key_env":    "CEREBRAS_API_KEY",
        "openai_compat":  True,
        "models": {
            "llama_small":   "llama3.1-8b",
            "qwen_instruct": "qwen-3-235b-a22b-instruct-2507",
        },
    },

    "nvidia": {
        "role": "specialist",
        "desc": "NVIDIA NIM. Llama, Qwen Coder 480B, Nemotron Super 49B.",
        "base_url":       "https://integrate.api.nvidia.com/v1",
        "api_key_env":    "NVIDIA_API_KEY",
        "openai_compat":  True,
        "models": {
            "llama_maverick":   "meta/llama-4-maverick-17b-128e-instruct",
            "llama_70b":        "meta/llama-3.3-70b-instruct",
            "deepseek_flash":   "deepseek-ai/deepseek-v4-flash",
            "deepseek_v3":      "deepseek-ai/deepseek-v3.2",
            "nemotron_49b":     "nvidia/llama-3.3-nemotron-super-49b-v1",
            "nemotron_120b":    "nvidia/nemotron-3-super-120b-a12b",
            "qwen_coder_480":   "qwen/qwen3-coder-480b-a35b-instruct",
            "vision_90b":       "meta/llama-3.2-90b-vision-instruct",   # VISION — image analysis
            "vision_11b":       "meta/llama-3.2-11b-vision-instruct",   # VISION — fast/light
            "phi4_multimodal":  "microsoft/phi-4-multimodal-instruct",  # VISION — phi-4 multimodal
            "kimi_k2":          "moonshotai/kimi-k2-instruct",
            "kimi_k2_6":        "moonshotai/kimi-k2.6",
            "devstral":         "mistralai/devstral-2-123b-instruct-2512",
            "mistral_large3":   "mistralai/mistral-large-3-675b-instruct-2512",
            "mistral_medium35": "mistralai/mistral-medium-3.5-128b",
            "minimax_m25":      "minimaxai/minimax-m2.5",
            "glm51":            "z-ai/glm-5.1",
            "glm5":             "z-ai/glm5",
        },
    },

    "groq": {
        "role": "twin",
        "desc": "TWIN. Groq Llama 3.3 70B. Briefs tasks, routes bots, main chat.",
        "base_url":       "https://api.groq.com/openai/v1",
        "api_key_env":    "GROQ_API_KEY",
        "key_rotation":   ["GROQ_API_KEY", "GROQ_API_KEY_1", "GROQ_API_KEY_2", "GROQ_API_KEY_3"],
        "openai_compat":  True,
        "models": {
            "llama": "llama-3.3-70b-versatile",
        },
    },

    "openrouter": {
        "role": "intermediary",
        "desc": "OpenRouter. Board members, creative, legal, SHADOW.",
        "base_url":      "https://openrouter.ai/api/v1",
        "api_key_env":   "OPENROUTER_API_KEY",
        "openai_compat": True,
        "models": {
            "hermes3":        "nousresearch/hermes-3-llama-3.1-405b:free",
            "minimax":        "minimax/minimax-01:free",
            "minimax_m25":    "minimax/minimax-m2.5:free",
            "qwen_free":      "qwen/qwen3-235b-a22b:free",
            "mistral_free":   "mistralai/mistral-7b-instruct:free",
            "dolphin_venice": "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
            "llama_free":     "meta-llama/llama-3.2-11b-vision-instruct:free",
            "legal":          "mistralai/mixtral-8x7b-instruct:free",
            "gpt_oss_120b":   "openai/gpt-oss-120b:free",
            "sheel_m":        "sao10k/l3.3-euryale-70b:free",
        },
    },

    "aiml": {
        "role": "builder",
        "desc": "AIML. Builder bot coder. Key rotation x3.",
        "base_url":       "https://api.aimlapi.com/v1",
        "api_key_env":    "AIML_API_KEY_1",
        "key_rotation":   ["AIML_API_KEY_1", "AIML_API_KEY_2", "AIML_API_KEY_3"],
        "openai_compat":  True,
        "models": {
            "glm":     "glm-5.1",
            "sheel_m": "gpt-4o",
        },
    },

    "deepinfra": {
        "role": "specialist",
        "desc": "DeepInfra specialist models.",
        "base_url":      "https://api.deepinfra.com/v1/openai",
        "api_key_env":   "DEEPINFRA_API_KEY",
        "openai_compat": True,
        "models": {
            "nemotron":     "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
            "dolphin":      "cognitivecomputations/dolphin-mistral-24b-venice-edition",
            "minimax_m2_5": "minimax/minimax-m2.5",
            "gpt_oss_120b": "openai/gpt-oss-120b",
        },
    },

    "grok": {
        "role": "shadow_video",
        "desc": "Grok beta + aurora. Shadow Video division.",
        "base_url":      "https://api.x.ai/v1",
        "api_key_env":   "GROK_API_KEY",
        "openai_compat": True,
        "models": {
            "grok_beta": "grok-beta",
            "aurora":    "aurora",
        },
    },

    "fal": {
        "role": "video_primary",
        "desc": "Fal.ai wan-2.1/2.2.",
        "api_key_env":   "FAL_KEY",
        "openai_compat": False,
        "models": {
            "wan21": "fal-ai/wan-2.1",
            "wan22": "fal-ai/wan-2.2",
        },
    },

    "huggingface": {
        "role": "video_fallback",
        "desc": "HuggingFace wan-2.1 fallback.",
        "api_key_env":   "HF_TOKEN",
        "openai_compat": False,
        "models": {
            "wan21": "Wan-AI/Wan2.1-T2V-14B",
        },
    },

    "replicate": {
        "role": "video_alt",
        "desc": "Replicate minimax video + SVD.",
        "api_key_env":   "REPLICATE_API_TOKEN",
        "openai_compat": False,
        "models": {
            "minimax_video": "minimax/video-01",
            "svd":           "stability-ai/stable-video-diffusion:3f0457e4619daac51203dedb472816f3af3d23aaa2332d691ca2413d8fbdcb65",
        },
    },

    "venice": {
        "role": "shadow",
        "desc": "Venice.ai uncensored. Gemma 4 Uncensored (chat persona) + Venice 1.2 Uncensored (authoritarian SHADOW).",
        "base_url":      "https://api.venice.ai/api/v1",
        "api_key_env":   "VENICE_ADMIN_KEY",
        "openai_compat": True,
        "models": {
            "gemma4_uncensored":      "gemma-4-uncensored",                    # GEMMA — uncensored chat persona
            "venice_uncensored_12":   "venice-uncensored-1-2",                 # SHADOW — authoritarian dark authority
            "venice_uncensored_11":   "venice-uncensored",                     # ORACLE — 1.1 shadow boardroom strategist
            "venice_roleplay":        "venice-uncensored-role-play",           # roleplay uncensored
            "hermes_405b":            "hermes-3-llama-3.1-405b",               # SPECIALIST — marketing, SEO, legal
            "mistral_small":          "mistral-small-3-2-24b-instruct",        # SPECIALIST — legal, budget
            "deepseek_flash":         "deepseek-v4-flash",                     # SPECIALIST — fast marketing analysis
            "llama_70b":              "llama-3.3-70b",                         # SPECIALIST — legal/SEO backup
            "qwen_coder_480_turbo":   "qwen3-coder-480b-a35b-instruct-turbo",  # BUILDER — uncensored coder 480B turbo
            "qwen_coder_480":         "qwen3-coder-480b-a35b-instruct",        # BUILDER — uncensored coder 480B
            "gpt_codex_52":           "openai-gpt-52-codex",                   # BUILDER — structured codex output
            "qwen_vl_235b":           "qwen3-vl-235b-a22b",                    # VISION — multimodal coder (image + code)
        },
    },
}

TASK_MODELS: dict = {
    # ── core hierarchy ────────────────────────────────────────────────────────
    "twin":             ("groq",   "llama"),            # TWIN — Groq Llama 70B
    "brief":            ("groq",   "llama"),            # briefing — Groq
    "shadow":           ("ollama_cloud", "qwen"),       # SHADOW — Ollama Qwen Coder 480B

    # ── relay / routing ───────────────────────────────────────────────────────
    "relay":            ("nvidia", "kimi_k2"),          # was: openrouter/hermes3

    # ── shadow chat / dark personas ───────────────────────────────────────────
    "shadow_chat":      ("venice", "venice_uncensored_12"),  # SHADOW — authoritarian dark authority (Venice 1.2)
    "board_dolphin":    ("venice", "gemma4_uncensored"),     # GEMMA — uncensored chat persona
    "chat_specialist":  ("venice", "gemma4_uncensored"),     # GEMMA — uncensored specialist
    "local_shadow":     ("ollama_local", "dolphin"),
    "local_adolphus":   ("ollama_local", "dolphin_venice"),

    # ── creative / occult comedy content ─────────────────────────────────────
    "creative":         ("nvidia", "glm51"),            # GLM 5.1 — creative/occult
    "creative_alt":     ("nvidia", "mistral_large3"),   # Mistral 675B — creative alt
    "creative_dolphin": ("venice", "gemma4_uncensored"),# GEMMA — uncensored creative
    "multimodal":       ("nvidia", "glm51"),            # GLM 5.1 — multimodal tasks

    # ── code ──────────────────────────────────────────────────────────────────
    "code":             ("venice", "qwen_coder_480_turbo"), # UNCENSORED coder — Venice Qwen 480B Turbo
    "code_check":       ("venice", "qwen_coder_480_turbo"), # UNCENSORED code review
    "code_check_v2":    ("venice", "gpt_codex_52"),         # Codex 52 — structured deep review
    "code_reason":      ("nvidia", "kimi_k2"),              # Kimi K2 — reasoning (stays on NVIDIA)
    "builder":          ("venice", "qwen_coder_480_turbo"), # UNCENSORED builder bot
    "builder_review":   ("venice", "qwen_coder_480_turbo"), # UNCENSORED builder review
    "builder_check":    ("venice", "gpt_codex_52"),         # Codex 52 — final structured check

    # ── vision / multimodal ───────────────────────────────────────────────────
    "vision":           ("nvidia", "vision_90b"),           # NVIDIA 90B vision — image analysis
    "vision_fast":      ("nvidia", "vision_11b"),           # NVIDIA 11B vision — fast/light
    "vision_uncensored":("venice", "qwen_vl_235b"),         # Venice VL 235B — uncensored vision+code

    # ── business / legal / SEO ────────────────────────────────────────────────
    "business":         ("venice", "hermes_405b"),      # Hermes 405B — marketing/legal/SEO specialist
    "business_deep":    ("venice", "mistral_small"),    # Mistral Small 24B — budget legal analysis
    "legal_finance":    ("venice", "hermes_405b"),      # Hermes 405B — best for legal on Venice
    "fast_reasoning":   ("venice", "deepseek_flash"),   # DeepSeek Flash — fast marketing analysis

    # ── boardroom members ─────────────────────────────────────────────────────
    "board_hermes":     ("nvidia", "kimi_k2"),          # Creative & Occult Consultant
    "board_gptoss":     ("venice", "hermes_405b"),      # Marketing & SEO Lead — Hermes 405B
    "board_qwen":       ("nvidia", "glm51"),            # Strategic Advisor
    "board_mistral":    ("nvidia", "mistral_large3"),   # Practical Critic — Mistral 675B
    "board_minimax":    ("nvidia", "minimax_m25"),      # Analytics & Data
    "board_venice":     ("venice", "venice_uncensored_11"), # ORACLE — Venice 1.1 shadow boardroom

    # ── video ─────────────────────────────────────────────────────────────────
    "video":            ("fal",         "wan21"),
    "video_fallback":   ("huggingface", "wan21"),
    "video_alt":        ("replicate",   "minimax_video"),
    "shadow_video":     ("grok",        "aurora"),
}

BOARD_MEMBERS = [
    {"key": "board_hermes",  "name": "HERMES",   "role": "Creative & Occult Consultant"},
    {"key": "board_gptoss",  "name": "GPTOSS",   "role": "Marketing & SEO Lead"},
    {"key": "board_qwen",    "name": "QWEN",     "role": "Strategic Advisor"},
    {"key": "board_mistral", "name": "MISTRAL",  "role": "Practical Critic"},
    {"key": "board_minimax", "name": "MINIMAX",  "role": "Analytics & Data"},
]

SHADOW_BOARD_MEMBERS = [
    {"key": "shadow_chat",   "name": "SHADOW",   "role": "Authoritarian Dark Authority (Venice 1.2)"},
    {"key": "board_venice",  "name": "ORACLE",   "role": "Shadow Boardroom Strategist (Venice 1.1)"},
    {"key": "board_dolphin", "name": "GEMMA",    "role": "Uncensored Persona (Gemma 4)"},
    {"key": "board_qwen",    "name": "QWEN",     "role": "Free Thinker"},
    {"key": "board_mistral", "name": "MISTRAL",  "role": "Shadow Critic"},
]

BRAINSTORM_MEMBERS = [
    {"key": "twin",          "name": "TWIN"},
    {"key": "board_hermes",  "name": "HERMES"},
    {"key": "board_qwen",    "name": "QWEN"},
    {"key": "board_mistral", "name": "MISTRAL"},
]

SHADOW_BRAINSTORM_MEMBERS = [
    {"key": "shadow_chat",   "name": "SHADOW"},   # Venice 1.2 — authoritarian
    {"key": "board_venice",  "name": "ORACLE"},   # Venice 1.1 — shadow boardroom strategist
    {"key": "board_dolphin", "name": "GEMMA"},    # Gemma 4 — uncensored persona
    {"key": "board_qwen",    "name": "QWEN"},
    {"key": "board_mistral", "name": "MISTRAL"},
]


def get_provider_cfg(provider_key: str) -> dict:
    cfg = PROVIDERS.get(provider_key)
    if not cfg:
        raise ValueError(f"Unknown provider: {provider_key}")
    return cfg


def get_task_routing(task_type: str) -> tuple[str, str]:
    routing = TASK_MODELS.get(task_type)
    if not routing:
        raise ValueError(f"Unknown task type: {task_type}")
    return routing
