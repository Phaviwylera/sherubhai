#!/usr/bin/env python3
"""Keep the LTX 0.9.8 VAE decoder in FP32 on the T4 installation."""
from pathlib import Path

source = Path("/opt/ltx-video/ltx_video/inference.py")
if not source.exists():
    raise SystemExit("LTX inference installation is missing")
original = source.read_text(encoding="utf-8")
old = "    vae = vae.to(torch.float16)\n    text_encoder = text_encoder.to(torch.float16)"
new = "    vae = vae.to(torch.float32)\n    text_encoder = text_encoder.to(torch.float16)"
if new in original:
    print("LTX VAE already uses FP32")
elif original.count(old) == 1:
    source.write_text(original.replace(old, new), encoding="utf-8")
    print("Patched LTX VAE to FP32")
else:
    raise SystemExit("Unexpected LTX inference version; refusing an unverified precision patch")
