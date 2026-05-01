"""
Twin Shadow — Part 1: Model Registry
All providers, models, and task routing in one place.
"""

PROVIDERS: dict = {

    "ollama_cloud": {
        "role": "shadow_boss",
        "desc": "Qwen. The real boss. Audit layer.",
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
        "desc": "Dolphin local. Board seat. Desktop only.",
        "base_url":      "http://localhost:11434/v1",
        "api_key":       "ollama",
        "openai_compat": True,
        "models": {
            "dolphin":        "dolphin-mixtral",
            "dolphin_venice": "dolphin-mistral-24b-venice-edition",
        },
    },

    "groq": {
        "role": "ceo",
        "desc": "Groq llama. CEO. Briefs tasks, routes bots.",
        "base_url":      "https://api.groq.com/openai/v1",
        "api_key_env":   "GROQ_API_KEY",
        "openai_compat": True,
        "models": {
            "llama": "llama-3.3-70b-versatile",
        },
    },

    "openrouter": {
        "role": "intermediary",
        "desc": "Relay + checker + creative + legal.",
        "base_url":      "https://openrouter.ai/api/v1",
        "api_key_env":   "OPENROUTER_API_KEY",
        "openai_compat": True,
        "models": {
            "uncensored":     "nousresearch/hermes-3-llama-3.1-405b:free",
            "minimax":        "minimax/minimax-01:free",
            "minimax_m25":    "minimax/minimax-m2.5:free",
            "qwen_free":      "qwen/qwen3-235b-a22b:free",
            "mistral_free":   "mistralai/mistral-7b-instruct:free",
            "dolphin_venice": "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
            "llama_free":     "meta-llama/llama-3.2-11b-vision-instruct:free",
            "legal":          "mistralai/mixtral-8x7b-instruct:free",
            "gpt_oss_legal":  "openai/gpt-oss-120b:free",
        },
    },

    "aiml": {
        "role": "builder",
        "desc": "AIML GLM-5.1. Coder.",
        "base_url":      "https://api.aimlapi.com/v1",
        "api_key_env":   "AIML_API_KEY",
        "openai_compat": True,
        "models": {
            "glm": "glm-5.1",
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
    "brief":            ("groq",        "llama"),
    "relay":            ("openrouter",  "uncensored"),
    "creative":         ("openrouter",  "qwen_free"),
    "creative_alt":     ("openrouter",  "mistral_free"),
    "creative_dolphin": ("openrouter",  "dolphin_venice"),
    "code":             ("aiml",        "glm"),
    "code_check":       ("openrouter",  "minimax"),
    "code_check_v2":    ("openrouter",  "minimax_m25"),
    "code_reason":      ("deepinfra",   "nemotron"),
    "business":         ("openrouter",  "legal"),
    "business_deep":    ("openrouter",  "gpt_oss_legal"),
    "shadow":           ("ollama_cloud","qwen"),
    "local_shadow":     ("ollama_local","dolphin"),
    "local_adolphus":   ("ollama_local","dolphin_venice"),
    "multimodal":       ("deepinfra",   "nemotron"),
    "chat_specialist":  ("deepinfra",   "dolphin"),
    "fast_reasoning":   ("deepinfra",   "minimax_m2_5"),
    "legal_finance":    ("deepinfra",   "gpt_oss_120b"),
    "video":            ("fal",         "wan21"),
    "video_fallback":   ("huggingface", "wan21"),
    "video_alt":        ("replicate",   "minimax_video"),
    "shadow_video":     ("grok",        "aurora"),
}


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
