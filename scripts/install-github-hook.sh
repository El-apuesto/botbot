#!/bin/bash
# Installs the post-commit hook that mirrors every commit to GitHub.
# Safe to run multiple times (idempotent).

HOOK_PATH=".git/hooks/post-commit"

cat > "$HOOK_PATH" << 'HOOK'
#!/bin/bash
# Auto-push to GitHub mirror after every commit.
# PAT is passed via GIT_ASKPASS (never embedded in the remote URL or argv).

if [ -z "$GITHUB_PAT" ]; then
  echo "[github-sync] GITHUB_PAT not set — skipping push" >&2
  exit 0
fi

LOG_DIR="logs"
LOG_FILE="${LOG_DIR}/github-sync.log"
mkdir -p "$LOG_DIR"

COMMIT=$(git rev-parse --short HEAD 2>/dev/null)
TIMESTAMP=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

(
  # Write a temp credential helper that echoes the PAT to git on stdin.
  # mktemp uses mode 600 by default — PAT never appears in process argv.
  ASKPASS=$(mktemp)
  printf '#!/bin/sh\necho "%s"\n' "$GITHUB_PAT" > "$ASKPASS"
  chmod +x "$ASKPASS"

  GIT_ASKPASS="$ASKPASS" git push --force \
    https://El-apuesto@github.com/El-apuesto/botbot.git \
    HEAD:main --quiet 2>&1
  STATUS=$?

  rm -f "$ASKPASS"

  if [ "$STATUS" -eq 0 ]; then
    echo "${TIMESTAMP} OK  pushed ${COMMIT} → github:El-apuesto/botbot main"
  else
    echo "${TIMESTAMP} ERR push failed for ${COMMIT} (exit ${STATUS})"
  fi

  exit "$STATUS"
) >> "$LOG_FILE" 2>&1 &

exit 0
HOOK

chmod +x "$HOOK_PATH"
echo "[github-sync] post-commit hook installed at $HOOK_PATH"
