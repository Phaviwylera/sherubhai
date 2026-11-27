#!/usr/bin/env bash
# Recreate the local Wan runtime after EC2 stop/start erases the instance-store NVMe.
set -euo pipefail
NVME=/opt/dlami/nvme
SOURCE="$NVME/Wan2.2"
MODEL="$NVME/Wan2.2-TI2V-5B"
PYTHON=/opt/ltx-video/.venv/bin/python
SOURCE_REVISION=1ea34ff48f87168174e12956e200b1d908b1c5ff
MODEL_REVISION=921dbaf3f1674a56f47e83fb80a34bac8a8f203e

mountpoint -q "$NVME"
mkdir -p "$NVME/sheru-hf-cache" "$NVME/pip-cache"
export HF_HOME="$NVME/sheru-hf-cache" PIP_CACHE_DIR="$NVME/pip-cache" TMPDIR="$NVME"
SWAP="$NVME/sheru-wan-swap"
if [[ ! -e "$SWAP" ]]; then
    fallocate -l 32G "$SWAP"
    chmod 600 "$SWAP"
    mkswap "$SWAP"
fi
if ! swapon --show=NAME --noheadings | grep -Fxq "$SWAP"; then swapon "$SWAP"; fi

if [[ ! -d "$SOURCE/.git" ]]; then
    git clone https://github.com/Wan-Video/Wan2.2.git "$SOURCE"
fi
git -C "$SOURCE" fetch --depth 1 origin "$SOURCE_REVISION"
git -C "$SOURCE" checkout --detach "$SOURCE_REVISION"
if ! "$PYTHON" -c 'import cv2,accelerate,easydict,ftfy,dashscope,decord; import numpy; assert int(numpy.__version__.split(".")[0]) < 2'; then
    "$PYTHON" -m pip install 'opencv-python-headless>=4.9,<5' 'accelerate>=1.1,<2' easydict ftfy dashscope decord 'numpy<2'
fi
# Wan's package initializer imports optional speech models. Only TI2V is installed here.
printf '%s\n' 'from . import configs, distributed, modules' 'from .textimage2video import WanTI2V' > "$SOURCE/wan/__init__.py"
# Wan's model calls FlashAttention directly, bypassing its own SDPA fallback.
"$PYTHON" - "$SOURCE/wan/modules/model.py" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
source = p.read_text()
original = 'from .attention import flash_attention'
fallback = 'from .attention import attention as flash_attention'
if original in source:
    p.write_text(source.replace(original, fallback, 1))
else:
    assert fallback in source, 'Wan attention import changed; review upstream before running'
PY
# Keep completed temporal chunks in host memory so five-second decoding fits A10G.
"$PYTHON" - "$SOURCE/wan/modules/vae2_2.py" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
first = '''                    first_chunk=True,
                )
            else:
                out_ = self.decoder('''
first_offloaded = first.replace('                )', '                ).cpu()', 1)
following = '''                    feat_idx=self._conv_idx,
                )
                out = torch.cat([out, out_], 2)
        out = unpatchify(out, patch_size=2)'''
following_offloaded = following.replace('                )', '                ).cpu()', 1)
if first in s and following in s:
    p.write_text(s.replace(first, first_offloaded, 1).replace(
        following, following_offloaded, 1))
else:
    assert first_offloaded in s and following_offloaded in s, 'Wan VAE changed; review upstream before running'
PY
if [[ ! -f "$MODEL/.sheru-ready" ]]; then
    "$PYTHON" - "$MODEL" "$MODEL_REVISION" <<'PY'
import sys
from huggingface_hub import snapshot_download
snapshot_download('Wan-AI/Wan2.2-TI2V-5B', revision=sys.argv[2],
                  local_dir=sys.argv[1], max_workers=4)
PY
    test -s "$MODEL/Wan2.2_VAE.pth"
    test -s "$MODEL/models_t5_umt5-xxl-enc-bf16.pth"
    test -s "$MODEL/diffusion_pytorch_model-00003-of-00003.safetensors"
    touch "$MODEL/.sheru-ready"
fi
