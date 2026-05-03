"""
Twin Shadow — Part 8: Hardening & Health Monitor
Verifies all API provider endpoints on startup and every N hours.
Exposes get_status() for the /api/status web endpoint.
"""

from __future__ import annotations
import asyncio
import threading
import time
from datetime import datetime, timezone

from part1_registry import PROVIDERS, get_provider_cfg
from part2_router import _build_client

_endpoint_status: dict = {}
_monitor_thread: threading.Thread | None = None


async def verify_endpoint(provider_key: str, timeout_s: int = 10):
    cfg = get_provider_cfg(provider_key)
    now = datetime.now(timezone.utc).isoformat()

    if not cfg.get("openai_compat"):
        _endpoint_status[provider_key] = {
            "online": None,
            "note":   "non-OpenAI — not pingable",
            "last_check": now,
        }
        return None, f"— {provider_key} skipped"

    try:
        client     = _build_client(provider_key)
        test_model = list(cfg["models"].values())[0]
        await asyncio.wait_for(
            client.chat.completions.create(
                model=test_model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            ),
            timeout=timeout_s,
        )
        _endpoint_status[provider_key] = {"online": True, "last_check": now}
        return True, f"✅ {provider_key}"
    except Exception as e:
        err = str(e)[:150]
        _endpoint_status[provider_key] = {
            "online": False,
            "error":  err,
            "last_check": now,
        }
        return False, f"❌ {provider_key}: {err[:80]}"


async def verify_all_endpoints(timeout_s: int = 10) -> dict:
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
