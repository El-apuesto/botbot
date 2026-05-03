"""
Twin Shadow — Part 1: Model Registry
All providers, models, task routing, TTS voice assignments.
"""

# ── TTS Voice assignments per persona ──────────────────────────────────────────
# Used by /api/tts for multi-voice boardroom / brainstorm / podcast audio.
# Overridable in Settings via localStorage (sent as JSON in the TTS request).
TTS_VOICES: dict[str, str] = {
    "TWIN":     "en-US-Neural2-D",
    "SHADOW":   "en-US-Neural2-A",
    "CAPI":     "en-US-Wavenet-B",
    "HERMES":   "en-US-Neural2-J",
    "GPTOSS":   "en-US-Neural2-G",
    "QWEN":     "en-US-Wavenet-D",
    "MISTRAL":  "en-US-Neural2-F",
    "MINIMAX":  "en-US-Wavenet-A",
    "BUILDER":  "en-US-Neural2-I",
    "NARRATOR": "en-US-Neural2-C",
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
            "qwen": "leckminartor/qwen3.5-uncensored:397b-cloud",
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

    "groq": {
        "role": "twin",
        "desc": "TWIN. Groq Llama 3.3 70B. Briefs tasks, routes bots, main chat.",
        "base_url":       "https://api.groq.com/openai/v1",
        "api_key_env":    "GROQ_API_KEY_1",
        "key_rotation":   ["GROQ_API_KEY_1", "GROQ_API_KEY_2", "GROQ_API_KEY_3"],
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
}

TASK_MODELS: dict = {
    "twin":             ("groq",        "llama"),
    "brief":            ("groq",        "llama"),
    "relay":            ("openrouter",  "hermes3"),
    "shadow":           ("ollama_cloud","qwen"),
    "shadow_chat":      ("openrouter",  "dolphin_venice"),
    "creative":         ("openrouter",  "qwen_free"),
    "creative_alt":     ("openrouter",  "mistral_free"),
    "creative_dolphin": ("openrouter",  "dolphin_venice"),
    "code":             ("aiml",        "glm"),
    "code_check":       ("openrouter",  "minimax"),
    "code_check_v2":    ("openrouter",  "minimax_m25"),
    "code_reason":      ("openrouter",  "gpt_oss_120b"),
    "business":         ("openrouter",  "legal"),
    "business_deep":    ("openrouter",  "gpt_oss_120b"),
    "board_hermes":     ("openrouter",  "hermes3"),
    "board_gptoss":     ("openrouter",  "gpt_oss_120b"),
    "board_qwen":       ("openrouter",  "qwen_free"),
    "board_mistral":    ("openrouter",  "mistral_free"),
    "board_minimax":    ("openrouter",  "minimax"),
    "board_dolphin":    ("openrouter",  "dolphin_venice"),
    "local_shadow":     ("ollama_local","dolphin"),
    "local_adolphus":   ("ollama_local","dolphin_venice"),
    "multimodal":       ("openrouter",  "qwen_free"),
    "chat_specialist":  ("openrouter",  "dolphin_venice"),
    "fast_reasoning":   ("openrouter",  "minimax_m25"),
    "legal_finance":    ("openrouter",  "gpt_oss_120b"),
    "builder":          ("aiml",        "glm"),
    "builder_review":   ("openrouter",  "minimax_m25"),
    "builder_check":    ("openrouter",  "qwen_free"),
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
    {"key": "board_dolphin", "name": "DOLPHIN",  "role": "Uncensored Strategist"},
    {"key": "shadow_chat",   "name": "SHADOW",   "role": "Dark Authority"},
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
    {"key": "shadow_chat",   "name": "SHADOW"},
    {"key": "board_dolphin", "name": "DOLPHIN"},
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
