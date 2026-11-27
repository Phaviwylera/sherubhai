"""Optional local LTX image-to-video renderer for the EC2 installation."""
import math
import os
from pathlib import Path
import subprocess

MODEL_ROOT = Path("/opt/ltx-video")
CONFIG = MODEL_ROOT / "configs" / "sheru-t4.yaml"
PYTHON = MODEL_ROOT / ".venv" / "bin" / "python"


def enabled():
    return (
        os.getenv("SHERU_LOCAL_VIDEO") == "1"
        and PYTHON.exists()
        and CONFIG.exists()
        and (MODEL_ROOT / "ltxv-2b-0.9.8-distilled.safetensors").exists()
    )


def render(ep, index, folder, project, on_progress=None):
    from finishing import safe_file
    from quality import preflight, render_prompt

    preflight(ep)
    shot = ep["shots"][index]
    if not shot.get("first_frame"):
        raise ValueError(f"Shot {index + 1} needs an approved first-frame image.")
    first_frame = safe_file(project, shot["first_frame"])
    attempt = shot.get("render_attempt", 1)
    output = Path(folder) / f"ltx-{index + 1:02}-{attempt}"
    output.mkdir(parents=True, exist_ok=True)
    frames = math.ceil((shot["seconds"] * 24 - 1) / 8) * 8 + 1
    if on_progress:
        on_progress(f"Rendering shot {index + 1} locally with LTX Video 2B.")

    code = (
        "from ltx_video.inference import infer, InferenceConfig; "
        "import sys; "
        "infer(InferenceConfig(prompt=sys.argv[1], output_path=sys.argv[2], "
        "pipeline_config=sys.argv[3], height=640, width=352, "
        "num_frames=int(sys.argv[4]), frame_rate=24, offload_to_cpu=True, "
        "conditioning_media_paths=[sys.argv[5]], conditioning_start_frames=[0]))"
    )
    environment = dict(os.environ, HF_HOME="/opt/dlami/nvme/sheru-hf-cache")
    log = output / "inference.log"
    with log.open("w", encoding="utf8") as stream:
        result = subprocess.run(
            [str(PYTHON), "-c", code, render_prompt(ep, index), str(output),
             str(CONFIG), str(frames), str(first_frame)],
            cwd=MODEL_ROOT, env=environment, stdout=stream,
            stderr=subprocess.STDOUT, check=False,
        )
    if result.returncode:
        raise RuntimeError(
            f"Local LTX render failed with exit code {result.returncode}. "
            f"See {log}."
        )
    videos = sorted(output.glob("video_output*.mp4"))
    if len(videos) != 1:
        raise RuntimeError(f"Local LTX renderer produced {len(videos)} videos. See {log}.")
    # The model's native 352x640 frame is cropped slightly to exact 9:16.
    portrait = output / "portrait.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(videos[0]), "-vf",
         "crop=352:626", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-y", str(portrait)],
        check=True,
    )
    return portrait

