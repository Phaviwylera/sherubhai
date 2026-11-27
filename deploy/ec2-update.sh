#!/usr/bin/env bash
# Deploy the current main branch into a fixed EC2 installation.
# Rolls back to the previously deployed commit when the health check fails.
set -euo pipefail
repo=/opt/sheru-studio
state=/var/lib/sheru-studio
install -d "$state"
if [[ ! -d "$repo/.git" ]]; then
  git clone --depth 1 https://github.com/Phaviwylera/sherubhai.git "$repo"
fi
cd "$repo"
git fetch --depth 1 origin main
remote=$(git rev-parse FETCH_HEAD)
current=$(cat "$state/deployed-sha" 2>/dev/null || true)
if [[ "$remote" == "$current" ]]; then
  echo "Already on $remote"
  exit 0
fi

# releases.py uses hashlib.file_digest, which needs Python 3.11 or newer.
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "Python 3.11 or newer is required for this deployment." >&2
  exit 1
}

git reset --hard "$remote"
chown ubuntu:ubuntu "$repo/SheruStudio"
mkdir -p "$repo/SheruStudio/studio-data" "$repo/SheruStudio/logs" "$repo/SheruStudio/exports"
chown -R ubuntu:ubuntu "$repo/SheruStudio/studio-data" "$repo/SheruStudio/logs" "$repo/SheruStudio/exports"
python3 -m venv "$repo/.venv"
"$repo/.venv/bin/pip" install --disable-pip-version-check -q -r "$repo/SheruStudio/requirements.txt"
python3 -m compileall -q "$repo/SheruStudio"
if [[ -f /opt/ltx-video/ltx_video/inference.py ]]; then
  python3 "$repo/deploy/patch-ltx-t4.py"
fi

health() {
  systemctl is-active --quiet sheru-studio.service &&
    curl --fail --silent --max-time 10 http://127.0.0.1:8765/ >/dev/null
}

systemctl restart sheru-studio.service
sleep 3
if health; then
  printf '%s\n' "$remote" > "$state/deployed-sha"
  echo "Deployed $remote"
  exit 0
fi

echo "Health check failed for $remote." >&2
if [[ -n "$current" ]]; then
  echo "Rolling back to $current." >&2
  git fetch --depth 1 origin "$current" || true
  if git reset --hard "$current"; then
    "$repo/.venv/bin/pip" install --disable-pip-version-check -q -r "$repo/SheruStudio/requirements.txt" || true
    python3 -m compileall -q "$repo/SheruStudio" || true
    systemctl restart sheru-studio.service || true
    sleep 3
    if health; then
      echo "Rolled back to $current." >&2
    else
      echo "Rolled back but the service is still unhealthy; inspect the unit logs." >&2
    fi
  fi
fi
exit 1
