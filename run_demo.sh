#!/usr/bin/env bash
# One-command clean demo run, intended for the pitch-video recording.
#
# Wipes all prior state (so the audit trail shows exactly this run),
# starts the container, waits for it to be healthy, then runs the
# buyer-agent demo end to end.
#
#   ./run_demo.sh

set -euo pipefail

cd "$(dirname "$0")"

echo "==> Tearing down any existing container and wiping stored data"
docker compose down --volumes >/dev/null 2>&1 || true

echo "==> Starting Sentinel-AP2"
docker compose up -d --build

echo -n "==> Waiting for the server to come up"
for _ in $(seq 1 30); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        echo " ready."
        break
    fi
    echo -n "."
    sleep 1
done

if ! curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    echo
    echo "Server did not become healthy in time. Check: docker compose logs" >&2
    exit 1
fi

if [ -d .venv ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

echo
python scripts/demo_agent.py
