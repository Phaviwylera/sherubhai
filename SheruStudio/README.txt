AUTOMATIC REVIEW / AUDIO REPAIR / VISUAL REGENERATION UPDATE

The screenshot's stop came from a hard-coded break after any Tamil ASR mismatch.
That branch is removed. Every normal Render now enables persistent automatic
recovery, not only the separate Start automatic production button.

Short Tamil clips are recognized without VAD so quiet opening words are not
silently discarded. Conservative number/colloquial spelling normalization is
recorded as a transcript screen, not proof of native pronunciation. A failing
screen gets an independent medium-model recognition check. There is no desired
transcript supplied to the recognizer and no lowered similarity threshold.

Audio repair preserves the raw moving footage. It first remixes the complete
recorded line, then can rerecord the exact same wording at modest speaking rates.
Each repaired mix and clean source voice are rechecked. Native delivery is not
guaranteed by ASR. Visual and cut failures continue to actual video regeneration
with the observed corrections. Audio-only problems do not spend another silent
video generation. Raw clips, repaired candidates and the complete repair history
remain available in Review room and downloadable logs.

A quality failure queues persistent recovery instead of telling the user to edit
the contract. Completed and pending current-revision shots are found automatically
when Render is clicked again, including when a newer empty provider-error run
exists. Local repair of an existing clip can happen without opening a provider,
including during a video quota cooldown. There are bounded attempts and scheduled
retries; unresolved failures are never promoted to verified success.

The fresh installer now starts animated production. Illustrated/storyboard preview
is a secondary explicit tool, not a fallback for completing an animated episode.
Cloud quota, configured access and full-speed playback review still apply.

Validation includes recognition-only recovery, unchanged-dialogue remix, clean
and mixed speech checks, queued quality recovery, no-GPU resume, visual regeneration
and automatic candidate reuse. Actual local ASR validation results are documented
only if completed successfully; no successful new full-motion movie is claimed
from these controlled workflow tests.

COMPLETE EPISODE WITHOUT VIDEO GPU / ILLUSTRATED OR HYBRID MODE

Director's desk now includes Finish illustrated episode - no video GPU.
It retains available completed clips from the current-revision render, reuses
existing recorded Tamil voices, and fills remaining slots with shot images and
slow camera moves. All seven slots are joined into a 30-second 720x1280 master.

This is an explicitly illustrated/hybrid alternative. Characters in illustrated
segments remain still; it does not claim body animation or synchronized mouths.
It requires scene images and online Tamil TTS for any voice not already recorded.
No video-generation endpoint or quota is used by this path. The bundled pilot
includes a new matching closing reaction portrait. Original procedural glass/pen
accents and a quiet ambience support the illustrated cut. Existing supplied sound
cues, music and ambience remain usable. Review the completed master before upload.

A complete illustrated pilot was actually rendered in the execution environment.
All seven full Tamil lines fit their slots without speed adjustment. Export media
checks confirm a full-length 9:16 video and audio track. The Windows hybrid path
was tested with controlled cached clips; no remote Windows access is claimed.

QUOTA AND REVIEWER HOTFIX / 26 NOVEMBER 2026

A real run log showed shots 1 and 2 generated before shot 3 was refused with
"68s requested vs. 65s left". Those are GPU-compute seconds, not seconds of final
video. The provider reported a 23:55:46 wait. The earlier parser incorrectly
counted requested/remaining GPU numbers as the retry delay. This is corrected.
Existing timestamped run evidence repairs a too-short stored wait on startup.
Quota cards now show the requested/remaining numbers and actual retry-window end.
Provider unavailability is distinguished from an explicit quota refusal.

This update preserves your existing clips. After Repair.cmd, use Open completed
clips to preview them. Choose the original stopped render in Production Activity,
then Recheck saved clips to rerun local reviews without any video generation.
After the provider reset, use Resume selected render to continue the same run.
Starting a new render would start a new episode job instead of resuming it.

Tamil ASR now receives an FFmpeg-decoded float32 audio array. This bypasses the
PyAV metadata_errors incompatibility seen in the attached Windows log. Vision
review sends one numbered contact sheet rather than many separate images. This
supports single-image local vision servers; reduced detail remains an uncertainty.
HTTP failures now include the local server's error response instead of only
"HTTP 400". The previous log omitted that body, so its exact visual-model error
could not be determined from that log alone.

Validated with the exact uploaded quota exception, timer migration, real FFmpeg
audio decoding, single-image request construction and a no-generation saved-clip
review. No additional cloud generation was used for these tests. Your actual
Windows reviewer inference still needs validation with the retained clips.

SHERU STUDIO / AUTOMATIC PRODUCTION UPDATE / 26 NOVEMBER 2026

UPDATE AN EXISTING INSTALLATION
1. Extract this ZIP into a folder on D.
2. Run Repair.cmd. Select your existing installation, normally D:\SheruStudio.
3. Studio reopens. The stock seven-shot tea-stall pilot receives its bundled
   opening frames automatically. No seven separate frame uploads are needed.
4. Click Start automatic production. The listed cloud destinations process your
   chosen shot images and prompts. Connect your accounts in Connections when ready.

Repair updates application files and keeps tools, models, credentials, custom
scripts and project media. Its Windows process-restart path is syntax checked,
not tested on your laptop. This chat has not remotely installed the update there.
Do not delete your existing installation or studio-data folder.

FIRST INSTALL
Run Setup.cmd and press Enter for D:\SheruStudio, or choose another full local
folder. Setup installs Python, FFmpeg, Ollama and dependencies, starts the local
model service on port 11435, and downloads both the text and visual-review models.
Large transfers show measured bytes, speed and elapsed time; named setup stages
and process heartbeats remain visible. Reopen with Studio.cmd or Pilot.cmd.
Application files, model cache, temporary files, project data and logs use the
chosen folder. Windows may retain installer records and credential-vault data
on the system drive. The program does not move Windows itself to D.

SCREENSHOT FIX
The error was missing first-frame images for shots 1-7, not a render freeze.
Five generated scene compositions now cover all seven pilot slots: empty
counter, tea placement, tea held, ledger exchange and parked-car conversation.
Their poses, left/right positions, gaze, hand contacts and declared cut ellipses
are reflected in the pilot shot contracts. Final export is exact 9:16 at 720x1280.
Automatic migration only applies when the stock pilot's complete shot contracts
and original character reference match. Existing uploaded frames and custom
scripts are preserved. A changed scene still needs its own correct starting frame.
The identity portrait alone cannot establish arbitrary new scene compositions.

CLEAR LOGS AND RUN STATUS
Production Activity now shows frame readiness, processed shots, run selection,
time-stamped severity, search/filter controls and a visible diagnostic card.
Errors include a stable code, error ID, stage, affected shots, next action,
exception chain, HTTP status when available and traceback. Open-shot buttons
jump to affected shots. Earlier-revision failures remain labelled as history.
Download run log retrieves all recorded events and structured error details.
Application log collects production, media-processing, reviewer and upload errors.
Tokens and URL queries are redacted; inspect logs before sharing personal content.
Files are under logs/studio.log and logs/run-<id>.jsonl in your installation.
The main application log rotates at 5 MB with three backups. A 20-second
heartbeat reports that a blocking model, network or media step is still running.
The progress bar counts processed shots; it does not invent a GPU percentage.

CLOUD MODEL POOL
The built-in pool supports these Gradio image-to-video APIs:
- Wan 2.2 fast: zerogpu-aoti/wan2-2-fp8da-aoti-faster /generate_video
- LTX 2.5 official: Lightricks/LTX-2.5 /run
- LTX Video distilled official: Lightricks/LTX-Video-distilled /image_to_video

Live Hub and Gradio API checks reported RUNNING for all three during this update.
This is API validation, not proof of completed video generation. Availability,
operator policies and free account quota may change. No paid API is configured.
The selected provider is tried first. Availability failures can route to another
compatible model. Clear visual failures can use another model on the next bounded
attempt. New pilots allow three attempts; saved preferences are preserved.
Image input, duration and required parameters are checked before submitting.
LTX temporal requests use its 8k+1 frame grid, then trim excess without slowing
motion. LTX 2.5 prompt enhancement and automatic duration are explicitly disabled
to retain the compiled contract. Exposed seeds and negative prompts are recorded.
Known small portrait-format differences are centre-cropped and documented.

All configured Hugging Face routes share the same quota wait. A rate/quota error
persists a shared cooldown; the router does not rotate Spaces or accounts to evade
limits. Default unknown quota waits are six hours. A provider retry interval is
used when available. Free cloud video generation is NOT unlimited.

AUTOMATIC PRODUCTION AND RECOVERY
Start automatic production runs the current episode through provider preflight,
local review-model preparation, exact Tamil recordings, duration checks, video
rendering, review/correction attempts, sound/colour finishing, assembly and growth
research. Every remaining voice is timed before the first video submission.
Completed shots and their reports survive a provider failure. Automatic retries
resume missing shots, preserving their episode order. Changed revisions require
a new render; modified checkpoint files are not silently reused.

Retries persist across Studio restarts. Temporary outages use increasing waits;
quota errors use the shared cooldown. There are at most eight automatic retries
per production start. Input errors, access problems and unresolved quality defects
stop with diagnostics. Pause automatic retries prevents another scheduled attempt;
an already submitted task finishes and retains its result. Keep Studio open and
the computer awake. Resume selected render handles a stopped current-revision run.
This automates one prepared episode; it is not an unattended infinite-series writer
with unlimited new scene images. Custom episodes use Tamil script and frame tools.

TAMIL, QUALITY AND SOUND
The local writer and editor prepare native-style Chennai Tamil in Tamil script,
blocking, eyelines, hand contact, start/end poses, camera and motivated transitions.
Your exact line drives the online Tamil speech voice. You can supply a native
recording instead. Local ASR screens transcript similarity; sampled visual checks
cover physics, contact, anatomy, identity, eyelines, culture, expression and artifacts.
Cross-cut checks compare neighbouring clip boundaries. Missing or inconclusive
review evidence is marked uncertain. FFmpeg checks media, applies warm/neutral
colour, mixes supplied timed sound cues, ducks supplied music and normalizes audio.
SFX and music still require source recordings; they are not automatically created.

No model weights are fine-tuned. No phoneme-aligned lion lip-sync system is built
in. Dubbing by itself does not synchronize the mouth. Sampled images cannot prove
perfect physics or inspect every motion between frames. Listen and watch the
master, then complete the five final review checks before publication. A known
failed shot blocks assembly; uncertainty is never relabelled as verified success.

GROWTH AND RELEASES
Research samples related India Google Search Trends and recent Tamil-comedy
YouTube candidates. It records source URLs and timestamps, then drafts separate
Tamil titles, descriptions, captions, tags and hashtags. No private platform
ranking algorithm is known and no million-view result is guaranteed.
Instagram-wide live trends are not supplied by this integration.

Own-account CSV timing comparisons use platform,published_at,views_24h with UTC
offsets. Comparable weekday/hour samples need at least three publication dates
within 180 days. Insufficient data produces labelled test windows: YouTube 12:30
IST and Instagram 19:00 IST. These are experiments, not researched optimum hours.

Connect YouTube uses your own Desktop OAuth client JSON with YouTube Data API
access enabled. It requests upload and read-only permissions. Connect Instagram
uses your Meta app, registered redirect URI and professional account publishing
permission. One-time app configuration and account consent cannot be skipped.
Secrets are stored in Windows Credential Manager.

Schedule an approved current master with a unique episode ID and a direct HTTPS
URL to that same MP4 for Instagram. Use the growth plan's separate platform times
or an explicit future ISO timestamp with UTC offset. Instagram still needs media
hosting that its servers can fetch; this package does not provision a public host.
Optional automatic metadata refresh retains saved copy if research fails.
Queued releases resume their uploader after Studio restarts. An uncertain upload
is held for checking; later episodes on that platform wait, avoiding gaps or blind
duplicate uploads. Upload errors are now visible with diagnostic IDs and details.
Platform media IDs are recorded as submitted; verify actual account visibility.
Setup and rendering do not publish anything by themselves.

VERIFICATION AND REMAINING LIMITS
Local regression checks cover frame migration, preservation of custom work,
credential redaction, exception chains, log downloads, quota waits, availability
routing, checkpoint reuse/order/hash checks, repeated preflight failure, revision
protection, local review-model preparation and persisted upload failures.
Desktop/mobile browser checks covered frame readiness, diagnostic display,
filters, downloads, shot navigation, provider settings and JavaScript errors.
Python and JavaScript syntax and all PowerShell scripts passed checks.
Previous FFmpeg mixing/cropping checks remain applicable.

Actual cloud generation, native Tamil performance, real account OAuth/publication
and Windows installation still need validation in the real environment. An earlier
live generation attempt was stopped by automatic approval review because sending
the private Sheru reference to that public Space needed explicit destination
approval. This update performs API/schema checks without submitting those assets.
No successful live video render or upload is claimed.
