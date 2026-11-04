#!/bin/bash
# Starful HQ (folder starful-hq, 8091). Runs with the same environment as okadmin (8090):
# PATH with Homebrew (claude, gh, gcloud), okadmin's Python, okadmin/.env keys, SITES_YAML.
# Running it again restarts 8091 (stops the old 8091 server first).
set -euo pipefail
cd "$(dirname "$0")"

export WORK_ROOT="${WORK_ROOT:-/opt/work}"
OKADMIN_ROOT="${WORK_ROOT}/okadmin"

# shellcheck source=/opt/work/okadmin/scripts/okadmin_env.sh
source "${OKADMIN_ROOT}/scripts/okadmin_env.sh"
okadmin_env_init "${OKADMIN_ROOT}"
PY="$(okadmin_resolve_python)"

if [ -f "${OKADMIN_ROOT}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${OKADMIN_ROOT}/.env"
  set +a
fi
export WORK_ROOT="${WORK_ROOT:-/opt/work}"
export SITES_YAML="${SITES_YAML:-/opt/work/sites.yaml}"
if [ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ] && [[ "$GOOGLE_APPLICATION_CREDENTIALS" != /* ]]; then
  export GOOGLE_APPLICATION_CREDENTIALS="${OKADMIN_ROOT}/${GOOGLE_APPLICATION_CREDENTIALS}"
fi
# okadmin/.env says 8090; this app uses 8091 unless a port is given: ./start.sh 7777
export PORT="${1:-${NEXT_PORT:-8091}}"

pids="$(lsof -iTCP:${PORT} -sTCP:LISTEN -t 2>/dev/null || true)"
if [ -n "${pids}" ]; then
  echo "${PORT} 재시작: 이전 서버(${pids}) 종료"
  # shellcheck disable=SC2086
  kill ${pids} 2>/dev/null || true
  sleep 1
fi

echo "Starful HQ → http://127.0.0.1:${PORT}  (python: ${PY})"
for tool in claude gh gcloud; do
  if command -v "$tool" >/dev/null 2>&1 || [ "$tool" = claude -a -x /opt/work/_hub/node_modules/.bin/claude ]; then
    echo "  ✅ ${tool}"
  else
    echo "  ⚠️  ${tool} 없음"
  fi
done
[ -n "${GEMINI_API_KEY:-}" ] && echo "  ✅ GEMINI_API_KEY" || echo "  ⚠️  GEMINI_API_KEY 없음"
[ -n "${YOUTUBE_API_KEY:-}" ] && echo "  ✅ YOUTUBE_API_KEY" || echo "  ⚠️  YOUTUBE_API_KEY 없음"

exec "$PY" -m app
