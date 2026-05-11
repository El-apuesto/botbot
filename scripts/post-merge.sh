#!/bin/bash
set -e

pip install -q -r requirements.txt --quiet 2>/dev/null || true
