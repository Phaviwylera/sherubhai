#!/usr/bin/env bash
set -euo pipefail
repo=/opt/sheru-studio
if [[ ! -d "$repo/.git" ]]; then
  git clone --depth 1 https://github.com/Phaviwylera/sherubhai.git "$repo"
fi
cd "$repo"
git fetch --depth 1 origin main
remote=$(git rev-parse FETCH_HEAD)
current=$(cat /var/lib/sheru-studio/deployed-sha 2>/dev/null || true)
if [[ "$remote" == "$current" ]]; then exit 0; fi
git reset --hard "$remote"
mkdir -p "$repo/SheruStudio/studio-data" "$repo/SheruStudio/logs" "$repo/SheruStudio/exports"
chown -R ubuntu:ubuntu "$repo/SheruStudio/studio-data" "$repo/SheruStudio/logs" "$repo/SheruStudio/exports"
python3 -m venv "$repo/.venv"
"$repo/.venv/bin/pip" install --disable-pip-version-check -q -r "$repo/SheruStudio/requirements.txt"
python3 -m compileall -q "$repo/SheruStudio"
systemctl restart sheru-studio.service
sleep 3
systemctl is-active --quiet sheru-studio.service
curl --fail --silent --max-time 10 http://127.0.0.1:8765/ >/dev/null
printf '%s\n' "$remote" > /var/lib/sheru-studio/deployed-sha
