"""Download the pinned public VerbalTS Synth-M runtime. No training required."""
from pathlib import Path
import hashlib
import subprocess

import gdown
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[2] / "runtime/verbalts"
REVISION = "62a4a001a3bebaea5c728a2d769851204a2ae931"
WEIGHT_SHA256 = "722f35360601ac6de011256055a69d1a3e287f885aea2de1b289e6952ee69b9e"


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    upstream = ROOT / "upstream"
    if not upstream.exists():
        subprocess.run(["git", "clone", "https://github.com/seqml/VerbalTS.git", str(upstream)], check=True)
        subprocess.run(["git", "-C", str(upstream), "checkout", REVISION], check=True)
    revision = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise RuntimeError(f"Expected upstream {REVISION}; found {revision}. Existing checkout was preserved.")
    weight = ROOT / "model_best_loss.pth"
    if not weight.exists():
        gdown.download(id="1pFC3jpNCokDoDcgu8fHWg2OIfmFs_17A", output=str(weight))
    if hashlib.sha256(weight.read_bytes()).hexdigest() != WEIGHT_SHA256:
        raise RuntimeError("Checkpoint checksum mismatch")
    # Synth-M learns its own text encoder; only LongCLIP's tokenizer is needed.
    snapshot_download("zer0int/LongCLIP-GmP-ViT-L-14", local_dir=str(ROOT / "tokenizer"),
                      allow_patterns=["*.json", "merges.txt", "*.txt"])
    print(f"VerbalTS assets ready: {ROOT}")


if __name__ == "__main__":
    main()
