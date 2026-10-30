"""Serve ChatTS (time-series → text) as a small HTTP server.

ChatTS ships .bin weights and remote code written for transformers<=4.52, so it
needs torch>=2.6 and a transformers 4.5x environment — not the app's own. Run it
from such an environment (config/runtime.yaml services.chatts.python) and the app
calls it over HTTP. Weights come from the local Hugging Face cache only; nothing
is downloaded here.

The GPU is shared with other people, so the model is **loaded on the first
request and unloaded after `--idle-minutes` without one**. While idle the server
holds no GPU memory; the next request pays the load time again (tens of seconds
when the weights are in the page cache, minutes from a cold disk).
`--quant 8bit` keeps the resident footprint near 15GB instead of 30GB.

  GET  /health   -> {"available": true, "engine": "chatts", "device": ..., "model": ...,
                     "loaded": bool, "quant": "8bit"|"none", "idle_minutes": N}
  POST /describe -> {"timeseries": [[...], ...], "prompt": "... <ts><ts/> ...",
                     "max_tokens": 320, "temperature": 0.2}  =>  {"text": "..."}
"""
import argparse
import threading
import time

from flask import Flask, jsonify, request


def _install_cache_shims():
    """ChatTS remote code uses DynamicCache methods removed in transformers>=4.56."""
    from transformers.cache_utils import DynamicCache
    if not hasattr(DynamicCache, "seen_tokens"):
        DynamicCache.seen_tokens = property(lambda self: self.get_seq_length())
    if not hasattr(DynamicCache, "get_usable_length"):
        DynamicCache.get_usable_length = lambda self, new_seq_length, layer_idx=0: self.get_seq_length(layer_idx)
    if not hasattr(DynamicCache, "get_max_length"):
        DynamicCache.get_max_length = lambda self: None


class LazyModel:
    """Load on demand, drop after idle. One request at a time (14B on one GPU)."""

    def __init__(self, model_id: str, device: str, quant: str, idle_minutes: float):
        self.model_id, self.device, self.quant, self.idle_minutes = model_id, device, quant, idle_minutes
        self.lock = threading.Lock()
        self.model = self.tokenizer = self.processor = None
        self.last_used = 0.0
        threading.Thread(target=self._reaper, daemon=True).start()

    def _load(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor, AutoTokenizer
        _install_cache_shims()
        kwargs = {"trust_remote_code": True, "local_files_only": True, "device_map": self.device}
        if self.quant == "8bit":
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
        else:
            kwargs["torch_dtype"] = torch.float16
        started = time.monotonic()
        self.model = AutoModelForCausalLM.from_pretrained(self.model_id, **kwargs).eval()
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id, trust_remote_code=True, local_files_only=True)
        self.processor = AutoProcessor.from_pretrained(self.model_id, trust_remote_code=True,
                                                       local_files_only=True, tokenizer=self.tokenizer)
        print(f"[chatts] loaded ({self.quant}) in {time.monotonic() - started:.0f}s, "
              f"{torch.cuda.memory_allocated(self.model.device) / 1e9:.1f}GB", flush=True)

    def _unload(self):
        import gc
        import torch
        self.model = self.tokenizer = self.processor = None
        gc.collect()
        torch.cuda.empty_cache()
        print("[chatts] unloaded after idle", flush=True)

    def _reaper(self):
        while True:
            time.sleep(15)
            with self.lock:
                if self.model is not None and time.monotonic() - self.last_used > self.idle_minutes * 60:
                    self._unload()

    def generate(self, arrays, formatted: str, max_tokens: int, temperature: float) -> str:
        import torch
        with self.lock:
            if self.model is None:
                self._load()
            self.last_used = time.monotonic()
            with torch.inference_mode():
                inputs = self.processor(text=[formatted], timeseries=arrays, padding=True, return_tensors="pt")
                inputs = {k: (v.to(self.model.device, dtype=torch.float16) if v.is_floating_point()
                              else v.to(self.model.device)) for k, v in inputs.items()}
                kwargs = {"max_new_tokens": max_tokens}
                if temperature > 0:
                    kwargs.update(do_sample=True, temperature=temperature)
                else:
                    kwargs.update(do_sample=False)
                output = self.model.generate(**inputs, **kwargs)
                text = self.tokenizer.decode(output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
            self.last_used = time.monotonic()
            return text


def create_app(lazy: LazyModel):
    import numpy as np
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify(available=True, engine="chatts", device=lazy.device, model=lazy.model_id,
                       loaded=lazy.model is not None, quant=lazy.quant, idle_minutes=lazy.idle_minutes)

    @app.post("/describe")
    def describe():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error="JSON body must be an object"), 400
        prompt = data.get("prompt")
        series = data.get("timeseries") or []
        if not isinstance(prompt, str) or not prompt.strip():
            return jsonify(error="prompt must be a nonempty string"), 400
        if not isinstance(series, list) or not series or any(not isinstance(s, list) for s in series):
            return jsonify(error="timeseries must be a nonempty list of numeric lists"), 400
        if prompt.count("<ts><ts/>") != len(series):
            return jsonify(error="prompt must contain one <ts><ts/> tag per series"), 400
        arrays = []
        for s in series:
            arr = np.asarray([v for v in s if v is not None], dtype=np.float32)
            arr = arr[np.isfinite(arr)]
            if arr.size < 4:
                return jsonify(error="each series needs at least 4 finite points"), 400
            if arr.size > 1024:  # ChatTS recommends 64~1024 points
                arr = arr[np.linspace(0, arr.size - 1, 1024, dtype=int)]
            arrays.append(arr)
        max_tokens = max(16, min(1024, int(data.get("max_tokens", 320))))
        temperature = float(data.get("temperature", 0.2))
        formatted = (
            "<|im_start|>system\nYou are a helpful assistant.<|im_end|>"
            f"<|im_start|>user\n{prompt}<|im_end|><|im_start|>assistant\n"
        )
        started = time.monotonic()
        was_loaded = lazy.model is not None
        text = lazy.generate(arrays, formatted, max_tokens, temperature)
        return jsonify(text=text, model=lazy.model_id, device=lazy.device, quant=lazy.quant,
                       cold_start=not was_loaded, seconds=round(time.monotonic() - started, 2))

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="bytedance-research/ChatTS-14B")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--port", type=int, default=7014)
    parser.add_argument("--quant", choices=("none", "8bit"), default="none")
    parser.add_argument("--idle-minutes", type=float, default=5.0,
                        help="unload the model after this many idle minutes (0 = keep loaded)")
    args = parser.parse_args()
    idle = args.idle_minutes if args.idle_minutes > 0 else float("inf")
    lazy = LazyModel(args.model, args.device, args.quant, idle)
    create_app(lazy).run(host="127.0.0.1", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
