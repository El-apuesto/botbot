"""
Twin Shadow - runtime/workers.py
Arq background job workers.

Each long-running task (approve, quick_build, video render) becomes
an Arq job so it runs in a background process instead of blocking
the bot's asyncio loop.

To start the worker:
    arq runtime.workers.WorkerSettings

To enqueue a job from the bot/orchestrator:
    from arq import create_pool
    from runtime.workers import enqueue_approve

    redis = await create_pool(RedisSettings())
    job_id = await enqueue_approve(project_id, redis)

Redis connection is read from ARQ_REDIS_URL env var (default: localhost:6379).
"""

from __future__ import annotations
import logging
import os

from arq.connections import RedisSettings, ArqRedis
from pydantic import ValidationError

from .events import emit_event, EventType

log = logging.getLogger("tsai.workers")


def _redis_settings() -> RedisSettings:
    url = os.environ.get("ARQ_REDIS_URL", "")
    if url:
        return RedisSettings.from_dsn(url)
    return RedisSettings()



# -- Job: approve a project ----------------------------------------------------

async def execute_approve_task(ctx: dict, project_id: str) -> list[dict]:
    """
    Arq job: run ExecutionEngine.approve() for a project.

    Emits TASK_STARTED - module events (delegated to orchestrator) - TASK_COMPLETED/FAILED.
    Returns a list of serializable output dicts.
    """
    from part5_orchestrator import ExecutionEngine  # avoid circular at import time

    await emit_event(project_id, EventType.TASK_STARTED, {"source": "arq_worker"})
    log.info("[WORKER] approve started: %s", project_id)

    engine = ExecutionEngine()

    try:
        results = await engine.approve(project_id)
        output = [
            {
                "module":            r.module,
                "output":            r.output[:500],   # truncated for job result payload
                "shadow":            r.shadow,
                "approved":          r.approved,
                "cross_validated":   r.cross_validated,
                "validation_notes":  r.validation_notes,
            }
            for r in results
        ]
        await emit_event(
            project_id,
            EventType.TASK_COMPLETED,
            {"module_count": len(results)},
        )
        log.info("[WORKER] approve completed: %s (%d modules)", project_id, len(results))
        return output

    except Exception as exc:
        await emit_event(
            project_id,
            EventType.TASK_FAILED,
            {"error": str(exc)},
        )
        log.error("[WORKER] approve failed: %s - %s", project_id, exc)
        raise


# -- Job: quick_build ----------------------------------------------------------

async def execute_quick_build_task(ctx: dict, prompt: str, chat_id: int = 0) -> dict:
    """Arq job: run ExecutionEngine.quick_build()."""
    from part5_orchestrator import ExecutionEngine

    job_id = ctx.get("job_id", prompt[:20])
    await emit_event(job_id, EventType.TASK_STARTED, {"source": "arq_worker", "type": "quick_build"})
    log.info("[WORKER] quick_build started")

    engine = ExecutionEngine()

    try:
        result = await engine.quick_build(prompt, chat_id=chat_id)
        await emit_event(job_id, EventType.TASK_COMPLETED, {"audit_passed": result.get("audit_passed")})
        return result
    except Exception as exc:
        await emit_event(job_id, EventType.TASK_FAILED, {"error": str(exc)})
        log.error("[WORKER] quick_build failed: %s", exc)
        raise


# -- Enqueue helpers -----------------------------------------------------------

async def enqueue_approve(project_id: str, redis: ArqRedis) -> str:
    """
    Enqueue an approve job. Returns the Arq job ID.

    Usage:
        from arq import create_pool
        from runtime.workers import enqueue_approve, _redis_settings

        redis = await create_pool(_redis_settings())
        job_id = await enqueue_approve("P0001", redis)
    """
    job = await redis.enqueue_job("execute_approve_task", project_id)
    job_id = job.job_id if job else "unknown"
    log.info("[WORKER] enqueued approve: %s - job %s", project_id, job_id)
    return job_id


async def enqueue_quick_build(prompt: str, chat_id: int, redis: ArqRedis) -> str:
    """Enqueue a quick_build job. Returns the Arq job ID."""
    job = await redis.enqueue_job("execute_quick_build_task", prompt, chat_id)
    job_id = job.job_id if job else "unknown"
    log.info("[WORKER] enqueued quick_build - job %s", job_id)
    return job_id


# -- Worker settings (entry point for `arq runtime.workers.WorkerSettings`) ---

class WorkerSettings:
    redis_settings = _redis_settings()

    functions = [
        execute_approve_task,
        execute_quick_build_task,
    ]

    max_jobs = 10
    job_timeout = 600       # 10 min max per job
    keep_result = 3600      # keep results in Redis for 1 hour
    retry_jobs = True
    max_tries = 3
