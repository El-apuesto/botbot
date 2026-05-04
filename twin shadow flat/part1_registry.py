"""
Twin Shadow — Part 1: Model Registry
All providers, models, task routing, TTS voice assignments.
Task #13: Boardroom & Committee System Overhaul
"""

# ── TTS Voice assignments per persona ──────────────────────────────────────────
TTS_VOICES: dict[str, str] = {
    "TWIN":         "en-US-Neural2-D",
    "SHADOW":       "en-US-Neural2-A",
    "GEMMA":        "en-US-Wavenet-C",
    "CAPI":         "en-US-Wavenet-B",
    "HERMES":       "en-US-Neural2-J",
    "GPTOSS":       "en-US-Neural2-G",
    "QWEN":         "en-US-Wavenet-D",
    "MISTRAL":      "en-US-Neural2-F",
    "MINIMAX":      "en-US-Wavenet-A",
    "BUILDER":      "en-US-Neural2-I",
    "NARRATOR":     "en-US-Neural2-C",
    "STRATEGY":     "en-US-Neural2-B",
    "FINANCE":      "en-US-Neural2-H",
    "CREATIVE":     "en-US-Wavenet-E",
    "TECH":         "en-US-Wavenet-F",
    "OPS":          "en-US-Wavenet-G",
    "DISTRIBUTION": "en-US-Wavenet-H",
    "VENICE70":     "en-US-Neural2-E",
    "QWQ":          "en-US-Wavenet-I",
}

# ── Hard token limits per role category ────────────────────────────────────────
TOKEN_LIMITS: dict[str, int] = {
    # Board roles
    "board_main":           200,
    "board_committee":      50,
    "board_summary":        100,
    # Shadow board (slightly longer turns)
    "shadow_board":         250,
    # Brainstorm
    "brainstorm":           150,
    "brainstorm_moderator": 100,
    # Hermes capped dialogue protocol
    "hermes_boardroom":     30,
    "hermes_brainstorm":    60,
    "hermes_marketing":     90,
    "hermes_interpret":     80,
    "hermes_yesno":         5,
    "hermes_clarify":       15,
    "hermes_retry":         80,
    # General
    "routing":              50,
    "audit":                200,
    "chat":                 800,
    "shadow_chat":          800,
    "code":                 4000,
    "brief":                300,
    "twin":                 300,
    # Embed / search (NVIDIA nv-embedqa, non-chat — token limit unused but kept for consistency)
    "embed_search":         0,
}

PROVIDERS: dict = {

    "ollama_cloud": {
        "role": "capi",
        "desc": "CAPI. Supreme authority. Known only to TWIN and SHADOW.",
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
        "desc": "Dolphin local. Desktop only.",
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
        "desc": "Cerebras ultra-fast. Routing, summaries, committee condensing.",
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
        "desc": "NVIDIA NIM. Free chat boardroom seats + specialized non-chat models.",
        "base_url":       "https://integrate.api.nvidia.com/v1",
        "api_key_env":    "NVIDIA_API_KEY",
        "key_rotation":   ["NVIDIA_API_KEY", "NIVIDIA_API_KEY1"],
        "openai_compat":  True,
        "models": {
            # ── Boardroom seats (free chat) ────────────────────────────────────
            "kimi_k2":              "moonshotai/kimi-k2-instruct",
            "kimi_k2_6":            "moonshotai/kimi-k2.6",
            "kimi_k2_thinking":     "moonshotai/kimi-k2-thinking",
            "mistral_large3":       "mistralai/mistral-large-3-675b-instruct-2512",
            "mistral_medium35":     "mistralai/mistral-medium-3.5-128b",
            "magistral_small":      "mistralai/magistral-small-2506",
            "palmyra_creative_122b":"writer/palmyra-creative-122b",
            "palmyra_fin_70b":      "writer/palmyra-fin-70b",
            "devstral":             "mistralai/devstral-2-123b-instruct-2512",
            "codestral_22b":        "mistralai/codestral-2501",
            "nemotron_49b":         "nvidia/llama-3.3-nemotron-super-49b-v1",
            "nemotron_120b":        "nvidia/nemotron-3-super-120b-a12b",
            "nemotron_ultra_253b":  "nvidia/llama-3.1-nemotron-ultra-253b-v1",
            "gpt_oss_120b":         "openai/gpt-oss-120b",
            "gpt_oss_20b":          "openai/gpt-oss-20b",
            "qwen_coder_480":       "qwen/qwen3-coder-480b-a35b-instruct",
            "qwen35_397b":          "qwen/qwen3.5-397b-a54b",
            "glm51":                "z-ai/glm-5.1",
            "glm5":                 "z-ai/glm5",
            "minimax_m25":          "minimaxai/minimax-m2.5",
            "deepseek_flash":       "deepseek-ai/deepseek-v4-flash",
            "deepseek_v3":          "deepseek-ai/deepseek-v3.2",
            "llama_70b":            "meta/llama-3.3-70b-instruct",
            "llama_maverick":       "meta/llama-4-maverick-17b-128e-instruct",
            "granite_34b_code":     "ibm/granite-34b-code-instruct",
            "seed_oss_36b":         "bytedance-research/seed-oss-36b",
            "stepfun_flash":        "stepfun/step-3-mini-flash-turbo",
            "qwen3_next_80b":       "qwen/qwen3-72b",
            "qwen35_122b":          "writer/palmyra-creative-122b",
            # ── Vision ────────────────────────────────────────────────────────
            "vision_90b":           "meta/llama-3.2-90b-vision-instruct",
            "vision_11b":           "meta/llama-3.2-11b-vision-instruct",
            "phi4_multimodal":      "microsoft/phi-4-multimodal-instruct",
            # ── Specialized non-chat ──────────────────────────────────────────
            "riva_translate":       "nvidia/riva-translate",
            "nemoretriever_parse":  "nvidia/nemoretriever-parse",
            "nv_embedqa":           "nvidia/nv-embedqa-e5-v5",
            "nv_embedcode":         "nvidia/nv-embedqa-mistral-7b-v2",
            "ai_video_detector":    "nv-us-east-1/ai-video-detector",
            "nemotron_reward":      "nvidia/nemotron-4-reward",
            "nemoguard_content":    "nvidia/nemoguard-8b-content-safety",
            "deplot":               "microsoft/deplot",
        },
    },

    "groq": {
        "role": "twin",
        "desc": "TWIN. Groq Llama 70B + Whisper transcription + PlayAI TTS + QwQ 32B.",
        "base_url":       "https://api.groq.com/openai/v1",
        "api_key_env":    "GROQ_API_KEY",
        "key_rotation":   ["GROQ_API_KEY", "GROQ_API_KEY_1", "GROQ_API_KEY_2", "GROQ_API_KEY_3"],
        "openai_compat":  True,
        "models": {
            # Chat
            "llama":            "llama-3.3-70b-versatile",
            "qwq_32b":          "qwq-32b",
            "deepseek_r1_70b":  "deepseek-r1-distill-llama-70b",
            "gemma2_9b":        "gemma2-9b-it",
            "mixtral_8x7b":     "mixtral-8x7b-32768",
            # Specialized (non-chat — called via dedicated endpoints)
            "whisper_v3":       "whisper-large-v3",
            "whisper_turbo":    "whisper-large-v3-turbo",
            "playai_tts":       "playai-tts",
        },
    },

    "grok": {
        "role": "shadow_video",
        "desc": "Grok 3 + aurora image gen. Shadow Video division.",
        "base_url":      "https://api.x.ai/v1",
        "api_key_env":   "GROK_API_KEY",
        "openai_compat": True,
        "models": {
            "grok_3":     "grok-3",
            "grok_mini":  "grok-3-mini",
            "aurora":     "aurora",
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
        "desc": "Venice.ai uncensored. SHADOW (1.2), GEMMA (Gemma 4), Hermes 405B, Llama 70B.",
        "base_url":      "https://api.venice.ai/api/v1",
        "api_key_env":   "VENICE_ADMIN_KEY",
        "openai_compat": True,
        "models": {
            "gemma4_uncensored":      "gemma-4-uncensored",
            "venice_uncensored_12":   "venice-uncensored-1-2",
            "venice_uncensored_11":   "venice-uncensored",
            "venice_roleplay":        "venice-uncensored-role-play",
            "hermes_405b":            "hermes-3-llama-3.1-405b",
            "mistral_small":          "mistral-small-3-2-24b-instruct",
            "deepseek_flash":         "deepseek-v4-flash",
            "llama_70b":              "llama-3.3-70b",
            "qwen_coder_480_turbo":   "qwen3-coder-480b-a35b-instruct-turbo",
            "qwen_coder_480":         "qwen3-coder-480b-a35b-instruct",
            "gpt_codex_52":           "openai-gpt-52-codex",
            "qwen_vl_235b":           "qwen3-vl-235b-a22b",
        },
    },
}

# ── Non-chat model keys — skip standard chat ping in health checks ─────────────
NVIDIA_NON_CHAT = frozenset({
    "riva_translate", "nemoretriever_parse", "nv_embedqa", "nv_embedcode",
    "ai_video_detector", "nemotron_reward", "nemoguard_content", "deplot",
})

GROQ_NON_CHAT = frozenset({"whisper_v3", "whisper_turbo", "playai_tts"})

TASK_MODELS: dict = {
    # ── core hierarchy ────────────────────────────────────────────────────────
    "twin":             ("groq",         "llama"),
    "brief":            ("groq",         "llama"),
    "shadow":           ("venice",       "venice_uncensored_12"),
    "shadow_chat":      ("venice",       "venice_uncensored_12"),
    "capi":             ("ollama_cloud", "qwen"),               # CAPI — distinct from SHADOW

    # ── routing / summarization (Cerebras fast) ───────────────────────────────
    "routing":          ("cerebras",     "llama_small"),
    "summarize":        ("cerebras",     "llama_small"),
    "relay":            ("nvidia",       "kimi_k2"),

    # ── shadow / dark personas ────────────────────────────────────────────────
    "board_dolphin":    ("venice",       "gemma4_uncensored"),
    "chat_specialist":  ("venice",       "gemma4_uncensored"),
    "local_shadow":     ("ollama_local", "dolphin"),
    "local_adolphus":   ("ollama_local", "dolphin_venice"),

    # ── creative / occult comedy ─────────────────────────────────────────────
    "creative":         ("nvidia",       "glm51"),
    "creative_alt":     ("nvidia",       "mistral_large3"),
    "creative_dolphin": ("venice",       "gemma4_uncensored"),
    "multimodal":       ("nvidia",       "glm51"),

    # ── code chain: CAPI (Ollama, free, uncensored) → NVIDIA free fallback ────
    "code":             ("ollama_cloud", "qwen"),
    "code_fallback":    ("nvidia",       "qwen_coder_480"),
    "code_check":       ("ollama_cloud", "qwen"),
    "code_check_v2":    ("venice",       "gpt_codex_52"),
    "code_reason":      ("nvidia",       "kimi_k2"),
    "builder":          ("ollama_cloud", "qwen"),
    "builder_review":   ("ollama_cloud", "qwen"),
    "builder_check":    ("venice",       "gpt_codex_52"),

    # ── vision ────────────────────────────────────────────────────────────────
    "vision":           ("nvidia",       "vision_90b"),
    "vision_fast":      ("nvidia",       "vision_11b"),
    "vision_uncensored":("venice",       "qwen_vl_235b"),

    # ── business / legal / SEO ────────────────────────────────────────────────
    "business":         ("venice",       "hermes_405b"),
    "business_deep":    ("venice",       "mistral_small"),
    "legal_finance":    ("venice",       "hermes_405b"),
    "fast_reasoning":   ("venice",       "deepseek_flash"),

    # ── regular boardroom seats (6 free NVIDIA) ───────────────────────────────
    "board_strategy":       ("nvidia",   "kimi_k2"),
    "board_finance":        ("nvidia",   "mistral_large3"),
    "board_creative":       ("nvidia",   "palmyra_creative_122b"),
    "board_tech":           ("nvidia",   "devstral"),
    "board_ops":            ("nvidia",   "nemotron_49b"),
    "board_distribution":   ("nvidia",   "gpt_oss_120b"),

    # ── shadow boardroom only ─────────────────────────────────────────────────
    "board_venice_llama":   ("venice",   "llama_70b"),
    "board_hermes_capped":  ("venice",   "hermes_405b"),

    # ── brainstorm extras ─────────────────────────────────────────────────────
    "brainstorm_qwq":       ("groq",     "qwq_32b"),
    "brainstorm_minimax":   ("nvidia",   "minimax_m25"),
    "brainstorm_glm":       ("nvidia",   "glm51"),
    "brainstorm_kimi":      ("nvidia",   "kimi_k2"),
    "brainstorm_mistral":   ("nvidia",   "mistral_large3"),

    # ── legacy board keys (back-compat with part6/part7) ──────────────────────
    "board_hermes":     ("nvidia",       "kimi_k2"),
    "board_gptoss":     ("venice",       "hermes_405b"),
    "board_qwen":       ("nvidia",       "glm51"),
    "board_mistral":    ("nvidia",       "mistral_large3"),
    "board_minimax":    ("nvidia",       "minimax_m25"),
    "board_venice":     ("venice",       "venice_uncensored_11"),

    # ── committee sub-model slots (pre-discussion) ────────────────────────────
    "committee_strategy_1": ("nvidia",   "nemotron_ultra_253b"),
    "committee_strategy_2": ("nvidia",   "qwen35_397b"),
    "committee_strategy_3": ("nvidia",   "kimi_k2_thinking"),
    "committee_finance_1":  ("nvidia",   "palmyra_fin_70b"),
    "committee_finance_2":  ("nvidia",   "magistral_small"),
    "committee_finance_3":  ("nvidia",   "mistral_medium35"),
    "committee_creative_1": ("nvidia",   "palmyra_creative_122b"),
    "committee_creative_2": ("cerebras", "qwen_instruct"),
    "committee_creative_3": ("nvidia",   "glm51"),
    "committee_tech_1":     ("nvidia",   "codestral_22b"),
    "committee_tech_2":     ("nvidia",   "granite_34b_code"),
    "committee_tech_3":     ("nvidia",   "gpt_oss_20b"),
    "committee_ops_1":      ("nvidia",   "seed_oss_36b"),
    "committee_ops_2":      ("nvidia",   "stepfun_flash"),
    "committee_ops_3":      ("nvidia",   "qwen3_next_80b"),
    "committee_dist_1":     ("nvidia",   "gpt_oss_120b"),
    "committee_dist_2":     ("nvidia",   "qwen35_122b"),
    "committee_dist_3":     ("nvidia",   "deepseek_v3"),

    # ── specialized non-chat services ─────────────────────────────────────────
    "transcription":        ("groq",     "whisper_v3"),
    "transcription_fast":   ("groq",     "whisper_turbo"),
    "tts_groq":             ("groq",     "playai_tts"),
    "translation":          ("nvidia",   "riva_translate"),
    "doc_parse":            ("nvidia",   "nemoretriever_parse"),
    "safety_gate":          ("nvidia",   "nemoguard_content"),
    "quality_score":        ("nvidia",   "nemotron_reward"),
    "embed_search":         ("nvidia",   "nv_embedqa"),

    # ── video ─────────────────────────────────────────────────────────────────
    "video":            ("fal",          "wan21"),
    "video_fallback":   ("huggingface",  "wan21"),
    "video_alt":        ("replicate",    "minimax_video"),
    "shadow_video":     ("grok",         "aurora"),
}

# ── Regular boardroom: 6 free NVIDIA seats + SHADOW + GEMMA + CAPI (cover) ────
BOARD_MEMBERS = [
    {"key": "board_strategy",     "name": "STRATEGY",     "alias": None,       "role": "Strategic Advisor (Kimi K2)"},
    {"key": "board_finance",      "name": "FINANCE",      "alias": "Rich Max",  "role": "Finance & Risk Analyst (Mistral 675B)"},
    {"key": "board_creative",     "name": "CREATIVE",     "alias": None,        "role": "Creative Director (Palmyra 122B)"},
    {"key": "board_tech",         "name": "TECH",         "alias": "Linnerd",   "role": "Tech Lead (Devstral 123B)"},
    {"key": "board_ops",          "name": "OPS",          "alias": None,        "role": "Operations (Nemotron 49B)"},
    {"key": "board_distribution", "name": "DISTRIBUTION", "alias": None,        "role": "Distribution & Growth (GPT-OSS 120B)"},
    {"key": "shadow_chat",        "name": "SHADOW",       "alias": "Shadow",    "role": "Dark Authority (Venice 1.2)"},
    {"key": "board_dolphin",      "name": "GEMMA",        "alias": "Gemma",     "role": "Uncensored Voice (Gemma 4)"},
    {"key": "capi",               "name": "CAPI",         "alias": "Quincy",    "role": "Consultant & Moderator"},
]

# ── Shadow boardroom: all uncensored, no CAPI ─────────────────────────────────
SHADOW_BOARD_MEMBERS = [
    {"key": "shadow_chat",         "name": "SHADOW",   "role": "Authoritarian Dark Authority (Venice 1.2)"},
    {"key": "board_dolphin",       "name": "GEMMA",    "role": "Uncensored Chat Persona (Gemma 4)"},
    {"key": "board_venice_llama",  "name": "VENICE70", "role": "Venice Llama 70B Uncensored"},
    {"key": "board_hermes_capped", "name": "HERMES",   "role": "Oracle / Cryptic Spark (405B, capped)"},
]

# ── Trigger-based specialists (only called when topic matches) ─────────────────
BOARD_SPECIALISTS = [
    {
        "key": "business",
        "name": "LEGAL",
        "role": "Legal Advisor (Hermes 405B)",
        "trigger_topics": ["legal", "compliance", "contract", "trademark", "copyright", "privacy", "gdpr"],
    },
    {
        "key": "business_deep",
        "name": "BUDGET",
        "role": "Budget Analyst (Mistral Small)",
        "trigger_topics": ["budget", "cost", "pricing", "financial", "invoice", "revenue", "profit"],
    },
    {
        "key": "fast_reasoning",
        "name": "MARKETING",
        "role": "Marketing Analyst (DeepSeek Flash)",
        "trigger_topics": ["marketing", "seo", "campaign", "ad", "brand", "audience", "funnel", "viral"],
    },
]

# ── Brainstorm: 9 members ──────────────────────────────────────────────────────
BRAINSTORM_MEMBERS = [
    {"key": "board_hermes_capped", "name": "HERMES",    "alias": "Hermes"},
    {"key": "shadow_chat",         "name": "SHADOW",    "alias": "Shadow"},
    {"key": "capi",                "name": "CAPI",      "alias": "Quincy"},
    {"key": "board_venice_llama",  "name": "VENICE70",  "alias": None},
    {"key": "brainstorm_glm",      "name": "GLM",       "alias": None},
    {"key": "brainstorm_kimi",     "name": "STRATEGY",  "alias": None},
    {"key": "brainstorm_mistral",  "name": "FINANCE",   "alias": "Rich Max"},
    {"key": "brainstorm_minimax",  "name": "MINIMAX",   "alias": None},
    {"key": "brainstorm_qwq",      "name": "QWQ",       "alias": None},
]

# ── Shadow brainstorm: SHADOW leads, CAPI interprets Hermes ───────────────────
SHADOW_BRAINSTORM_MEMBERS = [
    {"key": "shadow_chat",         "name": "SHADOW",    "alias": "Shadow"},
    {"key": "board_hermes_capped", "name": "HERMES",    "alias": "Hermes"},
    {"key": "capi",                "name": "CAPI",      "alias": "Quincy"},
    {"key": "board_venice_llama",  "name": "VENICE70",  "alias": None},
    {"key": "brainstorm_glm",      "name": "GLM",       "alias": None},
    {"key": "brainstorm_kimi",     "name": "STRATEGY",  "alias": None},
    {"key": "brainstorm_mistral",  "name": "FINANCE",   "alias": "Rich Max"},
    {"key": "brainstorm_minimax",  "name": "MINIMAX",   "alias": None},
    {"key": "brainstorm_qwq",      "name": "QWQ",       "alias": None},
]

# ── Committee structure: each board seat → 2-3 pre-discussion sub-models ──────
COMMITTEE_STRUCTURE: dict[str, list[str]] = {
    "board_strategy":     ["committee_strategy_1", "committee_strategy_2", "committee_strategy_3"],
    "board_finance":      ["committee_finance_1",  "committee_finance_2",  "committee_finance_3"],
    "board_creative":     ["committee_creative_1", "committee_creative_2", "committee_creative_3"],
    "board_tech":         ["committee_tech_1",      "committee_tech_2",     "committee_tech_3"],
    "board_ops":          ["committee_ops_1",        "committee_ops_2",      "committee_ops_3"],
    "board_distribution": ["committee_dist_1",       "committee_dist_2",     "committee_dist_3"],
    # No committees for these seats
    "shadow_chat":        [],
    "board_dolphin":      [],
    "shadow":             [],
    "capi":               [],
    "board_venice_llama": [],
    "board_hermes_capped":[],
    # Child bot placeholder slots (future)
    "_child_bot_1":       [],
    "_child_bot_2":       [],
    "_child_bot_3":       [],
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


def get_specialists_for_topic(topic: str) -> list[dict]:
    """Return specialist members whose trigger_topics match the given topic."""
    topic_lower = topic.lower()
    return [
        s for s in BOARD_SPECIALISTS
        if any(t in topic_lower for t in s["trigger_topics"])
    ]
