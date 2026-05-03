"""
Twin Shadow — Part 8: Hardening & Health Monitor
Verifies all API provider endpoints on startup and every N hours.
Task #13: Skip NVIDIA non-chat and Groq non-chat models in health ping;
          skip dead providers (openrouter/aiml/deepinfra removed from registry).
"""

from __future__ import annotations
import os
import asyncio
import threading
import time
from datetime import datetime, timezone

from part1_registry import PROVIDERS, get_provider_cfg, NVIDIA_NON_CHAT, GROQ_NON_CHAT

_endpoint_status: dict = {}
_monitor_thread: threading.Thread | None = None


async def verify_endpoint(provider_key: str, timeout_s: int = 20):
    cfg = get_provider_cfg(provider_key)
    now = datetime.now(timezone.utc).isoformat()

    if not cfg.get("openai_compat"):
        _endpoint_status[provider_key] = {
            "online": None,
            "note":   "non-OpenAI — not pingable",
            "last_check": now,
        }
        return None, f"— {provider_key} skipped (non-OpenAI)"

    # Build key, preferring rotation keys (ASCII-safe)
    def _clean(s: str) -> str:
        return (s or "").strip().replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")

    def _is_ascii(s: str) -> bool:
        try:
            s.encode("ascii")
            return True
        except (UnicodeEncodeError, UnicodeDecodeError):
            return False

    api_key = None
    for env_var in cfg.get("key_rotation", []):
        v = _clean(os.environ.get(env_var, ""))
        if v and _is_ascii(v):
            api_key = v
            break
        elif v and not _is_ascii(v):
            scrubbed = v.encode("ascii", "ignore").decode("ascii").strip()
            if scrubbed:
                api_key = scrubbed
                break

    if not api_key:
        api_key_env = cfg.get("api_key_env", "")
        raw = _clean(os.environ.get(api_key_env, cfg.get("default_key", "none")))
        api_key = raw if _is_ascii(raw) else raw.encode("ascii", "ignore").decode("ascii").strip()

    if "base_url_env" in cfg:
        base_url = os.environ.get(cfg["base_url_env"], "http://localhost:11434/v1").replace(" ", "")
    else:
        base_url = cfg["base_url"]

    # Pick first chat-capable model (skip non-chat specialized models)
    models = cfg["models"]
    non_chat_keys: frozenset[str] = frozenset()
    if provider_key == "nvidia":
        non_chat_keys = NVIDIA_NON_CHAT
    elif provider_key == "groq":
        non_chat_keys = GROQ_NON_CHAT

    test_model = next(
        (v for k, v in models.items()
         if k not in non_chat_keys and "aurora" not in k),
        None
    )

    if test_model is None:
        _endpoint_status[provider_key] = {
            "online": None,
            "note":   "no chat-capable model found to test",
            "last_check": now,
        }
        return None, f"— {provider_key} skipped (no chat model)"

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s)
        await client.chat.completions.create(
            model=test_model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        _endpoint_status[provider_key] = {"online": True, "last_check": now, "model": test_model}
        return True, f"✅ {provider_key} ({test_model})"
    except BaseException as e:
        err = str(e) or f"{type(e).__name__}"
        err = err[:150]
        _endpoint_status[provider_key] = {
            "online": False,
            "error":  err,
            "last_check": now,
        }
        return False, f"❌ {provider_key}: {err[:80]}"


async def verify_all_endpoints(timeout_s: int = 20) -> dict:
    results = []
    for k in PROVIDERS:
        results.append(await verify_endpoint(k, timeout_s))

    online = sum(1 for s in _endpoint_status.values() if s.get("online") is True)
    tested = sum(1 for s in _endpoint_status.values() if s.get("online") is not None)

    for item in results:
        if isinstance(item, tuple):
            _, msg = item
            print(f"[HARDENING] {msg}")

    print(f"[HARDENING] {online}/{tested} providers online")
    return _endpoint_status.copy()


def get_status() -> dict:
    """Return current provider status snapshot — used by /api/status."""
    return _endpoint_status.copy()


def _monitor_loop(interval_hours: int):
    asyncio.run(verify_all_endpoints())
    while True:
        time.sleep(interval_hours * 3600)
        try:
            asyncio.run(verify_all_endpoints())
        except Exception as e:
            print(f"[HARDENING] Periodic check error: {e}")


def start_monitor(interval_hours: int = 6):
    """Start health monitor as daemon thread. Safe to call multiple times."""
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        return
    _monitor_thread = threading.Thread(
        target=_monitor_loop,
        args=(interval_hours,),
        daemon=True,
        name="HealthMonitor",
    )
    _monitor_thread.start()
    print("[HARDENING] Health monitor started — checking all providers in background")
