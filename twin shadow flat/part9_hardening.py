from __future__ import annotations
import os
import asyncio
from datetime import datetime, timezone, timedelta

from part1_registry import PROVIDERS, get_provider_cfg
from part2_router import _build_client

_endpoint_status: dict = {}

async def verify_endpoint(provider_key: str, timeout_s: int = 10):
    cfg = get_provider_cfg(provider_key)
    if not cfg.get("openai_compat"):
        return None, f"{provider_key} skipped"
    try:
        client = _build_client(provider_key)
        test_model = list(cfg["models"].values())[0]
        await asyncio.wait_for(
            client.chat.completions.create(
                model=test_model,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=1,
            ),
            timeout=timeout_s
        )
        _endpoint_status[provider_key] = {"online": True, "last_check": datetime.now(timezone.utc).isoformat()}
        return True, f"✅ {provider_key} online"
    except Exception as e:
        _endpoint_status[provider_key] = {"online": False, "last_check": datetime.now(timezone.utc).isoformat(), "error": str(e)[:150]}
        return False, f"❌ {provider_key} down"

async def verify_all_endpoints(timeout_s: int = 10):
    print("\n🔍 VERIFYING PROVIDERS...")
    tasks = [verify_endpoint(k, timeout_s) for k in PROVIDERS.keys()]
    await asyncio.gather(*tasks, return_exceptions=True)
    online = sum(1 for s in _endpoint_status.values() if s.get("online"))
    print(f"Online: {online}/{len(PROVIDERS)}")
    return {"online": online}

class HealthMonitor:
    def __init__(self, check_interval_hours: int = 6): pass
    async def run(self): 
        while True: await asyncio.sleep(3600)
    def stop(self): pass

async def startup_check():
    await verify_all_endpoints()
    print("✅ Startup check done")
    return {"endpoints": {"online": len([v for v in _endpoint_status.values() if v.get("online")]) }}

if __name__ == "__main__":
    asyncio.run(startup_check())
