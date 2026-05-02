#!/bin/bash
set -e

cd "twin shadow flat"
pip install -q -r requirements.txt --quiet 2>/dev/null || true
