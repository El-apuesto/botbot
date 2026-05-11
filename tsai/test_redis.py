import asyncio
import os
from arq import create_pool
from arq.connections import RedisSettings
from dotenv import load_dotenv
load_dotenv()

async def test():
    url = os.environ.get("ARQ_REDIS_URL", "")
    if not url:
        print("❌ ARQ_REDIS_URL not set in environment")
        return

    try:
        r = await create_pool(RedisSettings.from_dsn(url))
        await r.set("ping", "pong")
        val = await r.get("ping")
        print("✅ Redis OK:", val)
    except Exception as e:
        print("❌ Redis failed:", e)

asyncio.run(test())
