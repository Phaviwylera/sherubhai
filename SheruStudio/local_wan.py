"""Local Wan 2.2 image-conditioned video for the EC2 A10G installation."""
import json
import math
import os
from pathlib import Path
import subprocess

MODEL_ROOT = Path("/opt/dlami/nvme/Wan2.2")
CHECKPOINT = Path("/opt/dlami/nvme/Wan2.2-TI2V-5B")
PYTHON = Path("/opt/ltx-video/.venv/bin/python")


def enabled():
    return (
        os.getenv("SHERU_LOCAL_WAN") == "1"
        and PYTHON.exists()
        and (MODEL_ROOT / "generate.py").exists()
        and (CHECKPOINT / ".sheru-ready").exists()
    )


def render(ep, index, folder, project, on_progress=None):
    from finishing import safe_file
    from quality import preflight, render_prompt, renderer_controls

    preflight(ep)
    if not enabled():
        raise RuntimeError("Local Wan 2.2 is not fully installed.")
    shot = ep["shots"][index]
    if not shot.get("first_frame"):
        raise ValueError(f"Shot {index + 1} needs an approved first-frame image.")
    first_frame = safe_file(project, shot["first_frame"])
    attempt = shot.get("render_attempt", 1)
    frames = math.ceil((shot["seconds"] * 24 - 1) / 4) * 4 + 1
    seed = renderer_controls({}, ep["settings"], index, attempt)[1]["requested_seed"]
    output = Path(folder)
    output.mkdir(parents=True, exist_ok=True)
    raw = output / f"wan-{index + 1:02}-{attempt}.mp4"
    portrait = output / f"wan-vertical-{index + 1:02}-{attempt}.mp4"
    log = output / f"wan-{index + 1:02}-{attempt}.log"
    audit = output / f"provider-{index + 1:02}-{attempt}.json"
    prompt = render_prompt(ep, index)
    audit.write_text(json.dumps({
        "source": "local Wan 2.2 TI2V-5B", "checkpoint": str(CHECKPOINT),
        "requested_seed": seed, "positive_prompt": prompt,
        "image_conditioning": "shot first frame", "frame_num": frames,
        "fps": 24, "sample_steps": 30, "requested_dimensions": [704, 1280],
        "requested_duration": shot["seconds"],
    }, ensure_ascii=False, indent=2), encoding="utf8")
    if on_progress:
        on_progress(f"Rendering shot {index + 1} locally with Wan 2.2 on the A10G.")
    env = dict(os.environ, HF_HOME="/opt/dlami/nvme/sheru-hf-cache",
               TMPDIR="/opt/dlami/nvme",
               PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
    args = [
        str(PYTHON), "generate.py", "--task", "ti2v-5B",
        "--size", "704*1280", "--ckpt_dir", str(CHECKPOINT),
        "--offload_model", "True", "--convert_model_dtype",
        "--image", str(first_frame), "--prompt", prompt,
        "--frame_num", str(frames), "--sample_steps", "30",
        "--base_seed", str(seed), "--save_file", str(raw),
    ]
    with log.open("w", encoding="utf8") as stream:
        try:
            result = subprocess.run(args, cwd=MODEL_ROOT, env=env,
                                    stdout=stream, stderr=subprocess.STDOUT,
                                    check=False, timeout=7200)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"Local Wan render timed out. See {log}.") from error
    if result.returncode or not raw.is_file():
        raise RuntimeError(
            f"Local Wan render failed with exit code {result.returncode}. See {log}.")
    from local_video import verify_frames
    verify_frames(raw, first_frame)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(raw), "-vf", "crop=704:1252",
         "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(portrait)],
        check=True, timeout=600,
    )
    return portrait
