#!/usr/bin/env python3
"""
Twin Shadow — Final Working Version
"""

import os
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


def main():
    print("\n Twin Shadow Starting...")
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
