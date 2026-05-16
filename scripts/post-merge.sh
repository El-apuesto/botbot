#!/bin/bash
set -e

pip install -q -r requirements.txt --quiet 2>/dev/null || true

# Reinstall the GitHub sync hook (local hooks are not tracked by git)
bash scripts/install-github-hook.sh
