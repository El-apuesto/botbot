#!/usr/bin/env python3
"""
Twin Shadow — Final Working Version
"""

import os
import subprocess
import sys
import time
import logging
from pathlib import Path
from threading import Event

# Add tsai/ to path so 'runtime' package is importable
_tsai = Path(__file__).parent / "tsai"
if str(_tsai) not in sys.path:
    sys.path.insert(0, str(_tsai))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# part6_bot starts Flask on PORT (default 5000) at import time
from part6_bot import main as bot_main

# Safe gTTS
GTTS_AVAILABLE = False
try:
    from gtts import gTTS
    GTTS_AVAILABLE = True
    logger.info("gTTS loaded")
except ImportError:
    logger.warning("gTTS not installed - audio disabled")


def text_to_mp3(text: str, filename: str, model_key=None):
    if not GTTS_AVAILABLE:
        return None
    try:
        os.makedirs("audio", exist_ok=True)
        tts = gTTS(text=text, lang='en')
        path = f"audio/{filename}.mp3"
        tts.save(path)
        return f"/audio/{filename}.mp3"
    except Exception as e:
        logger.error(f"Audio failed: {e}")
        return None


def _install_github_hook():
    """Reinstall the post-commit GitHub sync hook and reconcile GitHub on startup.

    The hook lives in .git/hooks/ which is not tracked by git, so it can be lost
    when the environment is rebuilt.  scripts/install-github-hook.sh is
    version-controlled and recreates the hook idempotently.

    After installing we also attempt a one-time reconciliation push so any
    commits that landed while the hook was absent are immediately mirrored
    to GitHub without waiting for the next new commit.
    """
    installer = Path(__file__).parent / "scripts" / "install-github-hook.sh"
    if not installer.exists():
        logger.warning("github-sync: installer not found at %s — skipping", installer)
        return

    try:
        result = subprocess.run(
            ["bash", str(installer)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            logger.info("github-sync hook ready: %s", result.stdout.strip())
        else:
            logger.warning("github-sync installer exited %d: %s", result.returncode, result.stderr.strip())
            return
    except Exception as exc:
        logger.warning("github-sync: could not run installer: %s", exc)
        return

    pat = os.environ.get("GITHUB_PAT", "")
    if not pat:
        logger.info("github-sync: GITHUB_PAT not set — skipping startup reconciliation push")
        return

    def _reconcile():
        import tempfile, stat
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as fh:
                fh.write(f"#!/bin/sh\necho '{pat}'\n")
                askpass = fh.name
            os.chmod(askpass, stat.S_IRWXU)
            env = {**os.environ, "GIT_ASKPASS": askpass}
            proc = subprocess.run(
                ["git", "push", "--force",
                 "https://El-apuesto@github.com/El-apuesto/botbot.git",
                 "HEAD:main", "--quiet"],
                capture_output=True, text=True, timeout=30, env=env
            )
            os.unlink(askpass)
            if proc.returncode == 0:
                logger.info("github-sync: startup reconciliation push OK")
            else:
                logger.warning("github-sync: startup reconciliation push failed (exit %d): %s",
                               proc.returncode, proc.stderr.strip())
        except Exception as exc:
            logger.warning("github-sync: reconciliation push error: %s", exc)

    import threading
    threading.Thread(target=_reconcile, daemon=True, name="github-sync-reconcile").start()


def main():
    print("\n Twin Shadow Starting...")
    _install_github_hook()
    logger.info("Web UI is running (managed by part6_bot)")

    token = os.environ.get("TOKEN", "")
    if not token:
        logger.warning("TOKEN not set — Telegram bot disabled. Web UI is still running.")
        logger.info("Set the TOKEN environment variable to enable the Telegram bot.")
        # Keep the process alive so Flask (started in part6_bot) keeps serving
        stop = Event()
        try:
            stop.wait()
        except KeyboardInterrupt:
            logger.info("Shutting down.")
        return

    logger.info("Starting Telegram Bot...")
    bot_main()


if __name__ == "__main__":
    os.chdir(Path(__file__).parent)
    main()
