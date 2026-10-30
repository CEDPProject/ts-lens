"""Run the official Synth-M VerbalTS checkpoint on a loopback HTTP endpoint.

The released model generates two channels at 128 points. Finder uses channel 1;
other requested lengths are explicitly resampled, never claimed as native output.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import threading
import time

import numpy as np
from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parents[2]
MODEL_ID = "VerbalTS/Synth-M-run0"
NATIVE_LENGTH = 128


def load_generator(root: Path, device: str):
    import torch
    import yaml
    sys.path.insert(0, str(root / "upstream"))
    from models.conditional_generator import ConditionalGenerator

    config_dir = root / "upstream/configs/synth-m"
    diff = yaml.safe_load((config_dir / "diff/model_text2ts_dep.yaml").read_text())
    cond = yaml.safe_load((config_dir / "cond/text_msmdiffmv.yaml").read_text())
    # Exact overrides from upstream scripts/synth-m/eval.sh and run.py.
    diff.update(device=device, generator_pretrain_path="")
    diff["diffusion"].update(base_patch=4, L_patch_len=3, multipatch_num=3)
    cond["cond_modal"] = "simple_text"
    cond["text"].update(tokenizer_path=str(root / "tokenizer"), pos_emb="none")
    model = ConditionalGenerator(diff, cond).to(device).eval()
    state = torch.load(root / "model_best_loss.pth", map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    return model


def create_app(model, device: str):
    import torch
    app = Flask(__name__)
    lock = threading.Lock()

    @app.get("/health")
    def health():
        return jsonify(available=True, engine="verbalts", model=MODEL_ID,
                       native_length=NATIVE_LENGTH, language="en", device=device)

    @app.post("/generate")
    def generate():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="JSON object required"), 400
        text = data.get("description")
        length = data.get("length", NATIVE_LENGTH)
        seed = data.get("seed", 42)
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            return jsonify(error="Enter an English description (1–2000 characters)."), 400
        if re.search(r"[가-힣ㄱ-ㅎㅏ-ㅣ]", text):
            return jsonify(error="VerbalTS 실험 모델은 영어 입력을 사용합니다. 입력창 아래 영어 예문을 눌러보세요."), 400
        if type(length) is not int or not 5 <= length <= 2000:
            return jsonify(error="length must be an integer between 5 and 2000"), 400
        if type(seed) is not int or not 0 <= seed < 2**32:
            return jsonify(error="seed must be an integer between 0 and 2^32-1"), 400
        prompt = text.strip()
        if "variable 1" not in prompt.lower():
            prompt = "The description of the variable 1:\n" + prompt
        if len(model.attr_en.tokenizer(prompt)["input_ids"]) > 248:
            return jsonify(error="Please shorten the description to 248 tokens."), 400
        started = time.monotonic()
        # Serialize requests: RNG and CUDA model are process-wide resources.
        with lock, torch.inference_mode():
            devices = [torch.device(device).index or 0] if device.startswith("cuda") else []
            with torch.random.fork_rng(devices=devices):
                torch.manual_seed(seed)
                batch = {"ts": torch.zeros(1, NATIVE_LENGTH, 2),
                         "tp": torch.arange(NATIVE_LENGTH).unsqueeze(0), "cap": [prompt]}
                native = model.generate(batch, 1)[0, 0, 0].cpu().numpy().astype(float)
        if not np.isfinite(native).all():
            return jsonify(error="VerbalTS generated non-finite values"), 500
        values = native if length == NATIVE_LENGTH else np.interp(
            np.linspace(0, 1, length), np.linspace(0, 1, NATIVE_LENGTH), native)
        return jsonify(description=text, values=values.tolist(), length=length,
                       native_values=native.tolist(), native_length=NATIVE_LENGTH,
                       resampled=length != NATIVE_LENGTH, output_channel=1,
                       engine="verbalts", model=MODEL_ID, device=device, llm_used=False,
                       fallback_reason=None, seed=seed,
                       generation_seconds=round(time.monotonic() - started, 3),
                       query={"system_prompt": "", "user_message": prompt},
                       raw_response="", signal={"applied": False})

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "runtime/verbalts")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--port", type=int, default=7012)
    args = parser.parse_args()
    generator = load_generator(args.root, args.device)
    create_app(generator, args.device).run(host="127.0.0.1", port=args.port, threaded=True)
