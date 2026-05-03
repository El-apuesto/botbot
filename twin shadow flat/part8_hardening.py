"""
Twin Shadow — Part 8: Hardening & Health Monitor
Verifies all API provider endpoints on startup and every N hours.
Exposes get_status() for the /api/status web endpoint.
"""

from __future__ import annotations
import os
import asyncio
import threading
import time
from datetime import datetime, timezone

from part1_registry import PROVIDERS, get_provider_cfg
from part2_router import _build_client

_endpoint_status: dict = {}
_monitor_thread: threading.Thread | None = None


async def verify_endpoint(provider_key: str, timeout_s: int = 15):
    cfg = get_provider_cfg(provider_key)
    now = datetime.now(timezone.utc).isoformat()

    if not cfg.get("openai_compat"):
        _endpoint_status[provider_key] = {
            "online": None,
            "note":   "non-OpenAI — not pingable",
            "last_check": now,
        }
        return None, f"— {provider_key} skipped"

    # Use first rotation key if available, otherwise fall back to default
    def _clean(s: str) -> str:
        return (s or "").strip().replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")

    rotation_envs = cfg.get("key_rotation", [])
    api_key = None
    for env_var in rotation_envs:
        v = _clean(os.environ.get(env_var, ""))
        if v:
            api_key = v
            break
    if not api_key:
        api_key_env = cfg.get("api_key_env", "")
        api_key = _clean(os.environ.get(api_key_env, cfg.get("default_key", "none")))

    # Use base_url, stripping accidental spaces
    if "base_url_env" in cfg:
        base_url = os.environ.get(cfg["base_url_env"], "http://localhost:11434/v1").replace(" ", "")
    else:
        base_url = cfg["base_url"]

    # Pick first text-capable model (skip aurora — image only)
    models = cfg["models"]
    test_model = next(
        (v for k, v in models.items() if "aurora" not in k),
        list(models.values())[0]
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        await asyncio.wait_for(
            client.chat.completions.create(
                model=test_model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            ),
            timeout=timeout_s,
        )
        _endpoint_status[provider_key] = {"online": True, "last_check": now, "model": test_model}
        return True, f"✅ {provider_key} ({test_model})"
    except Exception as e:
        err = str(e)[:150]
        _endpoint_status[provider_key] = {
            "online": False,
            "error":  err,
            "last_check": now,
        }
        return False, f"❌ {provider_key}: {err[:80]}"


async def verify_all_endpoints(timeout_s: int = 15) -> dict:
    tasks   = [verify_endpoint(k, timeout_s) for k in PROVIDERS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

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
