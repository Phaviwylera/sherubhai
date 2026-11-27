# Sheru Bhai — Sheru Studio

A local production studio for a 30-second vertical (9:16, 720×1280) Tamil-comedy
episode: script writing, Tamil speech, image-to-video generation through a
Hugging Face Space, sampled review, audio repair, assembly, and a scheduled
publishing queue for YouTube Shorts and Instagram Reels.

The supported installation is Windows. `deploy/ec2-update.sh` describes a
separate Linux/EC2 installation that runs the optional local LTX renderer.

## Run it (Windows)

1. Unzip the package to a folder you can write to.
2. `SheruStudio\START-FRESH.cmd` — installs Python, FFmpeg, Ollama and the Python
   packages into the chosen folder (default `D:\SheruStudio-Fresh`) and starts the
   first animated episode.
3. `SheruStudio\Setup.cmd` — the same installation without starting a render.
4. `SheruStudio\Studio.cmd` — reopen Studio. `Repair.cmd` updates an existing
   installation: it copies the new files, reinstalls the Python packages, runs the
   health check and restarts Studio.

`Setup`, `Repair` and `START-FRESH` print their progress and can be rerun; completed
downloads are reused and verified by checksum.

## Run it from source (any platform)

```bash
cd SheruStudio
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python studio.py                 # http://127.0.0.1:8765
python studio.py --no-browser    # do not open a browser tab
```

Python 3.11 or newer is required (`hashlib.file_digest`).
FFmpeg and `ffprobe` must be on `PATH`, and a local Ollama server supplies the
script model, the visual reviewer and the Tamil transcription model.

## Tests and checks

```bash
pip install pytest pyflakes
python -m pytest -q
python -m pyflakes SheruStudio
node --check SheruStudio/web/studio.js
```

`.github/workflows/checks.yml` runs the tests, the linters, a PowerShell parse check
and an assertion that the `.ps1`/`.cmd` files stay ASCII (Windows PowerShell 5.1
decodes scripts without a byte-order mark using the system code page).

## Where data lives

Everything the running application writes stays beside it (or in the install folder
chosen at setup), and is ignored by Git:

| Path | Contents |
| --- | --- |
| `studio-data/studio.sqlite` | projects, runs, events |
| `studio-data/<project>/` | reference image, uploaded frames, voices, clips |
| `studio-data/<project>/runs/<run>/` | per-run snapshots, provider audits, reviews |
| `releases.sqlite` | scheduled and submitted uploads |
| `video-provider.json` | Space, endpoint and routing preferences |
| `provider-wait.json` | saved provider retry window and last quota reading |
| `automation.json` | automatic-production state |
| `logs/studio.log`, `logs/run-<id>.jsonl` | redacted application and run logs |

Credentials live in Windows Credential Manager (service `SheruStudio`), except the
optional `youtube-token.json` written by `releases.py login-youtube`, which is created
with owner-only permissions.

## Configuration

| Variable | Meaning |
| --- | --- |
| `SHERU_PORT` | Studio port (default 8765) |
| `SHERU_ROOT` | installation root, used for the Whisper cache |
| `OLLAMA_HOST` | local model service, e.g. `127.0.0.1:11435` |
| `SHERU_SPACE`, `SHERU_ENDPOINT`, `HF_TOKEN` | provider defaults when no config file exists |
| `IG_GRAPH_VERSION`, `IG_USER_ID`, `IG_TOKEN` | Instagram credentials override |
| `SHERU_LOCAL_VIDEO=1` | use the local LTX renderer when it is installed |
| `SHERU_DATA`, `SHERU_CONFIG`, `SHERU_WAIT`, `SHERU_RELEASES`, `SHERU_LOGS` | relocate runtime files (used by the tests) |

## Publishing queue

`releases.py` queues a reviewed master for both platforms, preserving episode order.
It is normally driven by Studio (`Connections → Release schedule`, then `Start
uploader`). From a shell:

```bash
python releases.py queue --episode S01E001 --master master.mp4 \
  --title "..." --when 2030-01-01T19:00:00+05:30 --https_url https://host/episode.mp4
python releases.py list
python releases.py resolve --episode S01E001 --platform youtube --remote_id_or_retry retry
python releases.py resolve --episode S01E001 --platform youtube --remote_id_or_retry VIDEO_ID
```

A job is only marked "check" immediately before the upload call, so a local problem
(changed master, unusable metadata, expired token) is retried with backoff instead of
stranding the queue. A job whose outcome is unknown stays in "check" and blocks later
episodes on that platform until you resolve it (also available as Retry / Mark
published in Studio).

## Documentation

- `SheruStudio/START-HERE.txt` — shortest path for a new user.
- `SheruStudio/README-FIRST.txt` — what the current build does.
- `SheruStudio/README.txt` — detailed behaviour, limits and prior update notes.
