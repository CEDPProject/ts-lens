"""One-process application launcher and owned model-server lifecycle."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

import requests
import yaml
from backend.app_config import cfg, cfg_text_ts_raw
from backend.runtime_paths import APP_DIR
from backend import runtime_state


def load_settings():
    with (APP_DIR / "config/runtime.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def python_path(service):
    value = service.get("python")
    if not value:
        return Path(sys.executable)
    path = Path(value).expanduser()
    return path if path.is_absolute() else APP_DIR / path


def gpu_available(python):
    try:
        result = subprocess.run([str(python), "-c", "import torch; print(int(torch.cuda.is_available()))"],
                                capture_output=True, text=True, timeout=20)
        return result.returncode == 0 and result.stdout.strip().endswith("1")
    except (OSError, subprocess.TimeoutExpired):
        return False


def ready(service, device=None):
    """Health must describe the expected model, not just an occupied port."""
    try:
        response = requests.get(service["url"], timeout=1)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return False
        if device == "cpu" and data.get("device") != "cpu":
            return False
        service["_actual_device"] = data.get("device")
        catalog = cfg_text_ts_raw()["models"][service["model_key"]]
        if catalog["provider"] in {"verbalts", "chatts"}:
            # 두 서버 모두 /health 가 {available, model, engine} 을 낸다
            return data.get("available") is True and data.get("model") == catalog["model_id"] and data.get("engine") == catalog["provider"]
        return any(isinstance(item, dict) and item.get("id") == catalog["model_id"] for item in (data.get("data") or []))
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return False


def endpoint_occupied(service):
    target = urlparse(service["url"])
    if target.hostname not in {"localhost", "127.0.0.1"}:
        return False
    try:
        with socket.create_connection((target.hostname, target.port or 80), timeout=0.5):
            return True
    except OSError:
        return False


def prerequisites(service, device="cuda:0"):
    python = python_path(service)
    if not python.is_file():
        return "모델용 Python 환경 없음"
    if any(not (APP_DIR / p).exists() for p in service["required_paths"]):
        return "모델 파일 없음"
    code = "import importlib.util,sys; sys.exit(not all(importlib.util.find_spec(n) for n in " + repr(service["modules"]) + "))"
    try:
        check = subprocess.run([str(python), "-c", code], capture_output=True, timeout=20)
        if check.returncode:
            return "모델 의존성 없음"
    except (OSError, subprocess.TimeoutExpired):
        return "모델 환경 확인 실패"
    if device.startswith("cuda") and not gpu_available(python):
        return "모델 환경에서 CUDA 사용 불가"
    return ""


class ModelServers:
    def __init__(self, settings):
        self.settings = settings
        self.owned = []

    def close(self):
        owned, self.owned = self.owned, []
        for process, handle in reversed(owned):
            try:
                # The leader can exit before its workers. Its process group
                # remains ours even if poll() already reports completion.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as error:
                print(f"Model process cleanup failed ({process.pid}): {error}", file=sys.stderr)
            finally:
                handle.close()

    def prepare(self, mode, check=False):
        services = self.settings["services"]
        # Hardware applies to each model; the web process itself needs no GPU.
        interpreters = dict.fromkeys([Path(sys.executable), *[
            python_path(service) for service in services.values() if service["enabled"]]])
        gpu_by_python = {p: (False if mode in {"cpu", "off"} else gpu_available(p)) for p in interpreters}
        gpu = any(gpu_by_python.values())
        effective = mode
        choices = {"text_to_ts": runtime_state.CPU_KEY, "ts_to_text": runtime_state.CPU_KEY}
        records = []
        for name, service in sorted(services.items(), key=lambda item: item[1]["priority"]):
            devices = cfg_text_ts_raw()["models"][service["model_key"]]["inference_devices"]
            device = "cuda:0" if gpu_by_python.get(python_path(service)) and "cuda" in devices and mode != "cpu" else "cpu"
            tasks = service.get("tasks") or [service["task"]]
            row = {"name": name, "status": "skipped", "reason": "AI 끄기 모드", "owned": False, "device": device, "model_key": service["model_key"]}
            records.append(row)
            if not service["enabled"]:
                row["reason"] = "서비스 비활성화"
                continue
            if mode == "off":
                continue
            if mode != "external" and device == "cpu" and "cpu" not in devices:
                row["reason"] = "GPU 필수 모델: 사용 가능한 GPU 없음"
                continue
            if all(choices[t] != runtime_state.CPU_KEY for t in tasks):
                row["reason"] = "우선 모델 사용 가능"
                continue
            # Serving endpoint and API provider must agree.
            catalog = cfg_text_ts_raw()
            provider = catalog["models"][service["model_key"]]["provider"]
            base = catalog["providers"][provider]["base_url"].rstrip("/")
            if not service["url"].startswith(base + "/"):
                row["reason"] = "runtime.yaml과 text_ts.yaml의 서버 주소 불일치"
                continue
            if ready(service, device if mode != "external" else None):
                row.update(status="ready", reason="기존 서버 재사용", device=service.get("_actual_device") or ("external" if mode == "external" else device))
                for task in tasks:
                    if choices[task] == runtime_state.CPU_KEY:
                        choices[task] = service["model_key"]
                continue
            if mode == "external":
                row["reason"] = "외부 모델 서버 사용 불가"
                continue
            if endpoint_occupied(service):
                row["reason"] = "포트 사용 중이나 모델/장치 확인 실패; 중복 서버를 시작하지 않음"
                continue
            row["reason"] = prerequisites(service, device)
            if row["reason"]:
                continue
            if check:
                row.update(status="startable", reason="시작 조건 충족; check 모드에서는 실행하지 않음")
                continue
            if urlparse(service["url"]).hostname not in {"localhost", "127.0.0.1"}:
                row["reason"] = "자동 시작은 로컬 주소만 지원"
                continue
            logs = APP_DIR / "runtime/logs"
            logs.mkdir(parents=True, exist_ok=True)
            handle = (logs / f"{name}.log").open("ab")
            try:
                process = subprocess.Popen([str(python_path(service)), *[arg.replace("{device}", device) for arg in service["command"]]],
                    cwd=APP_DIR, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
                    env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
            except OSError as error:
                handle.close()
                row["reason"] = str(error)
                continue
            self.owned.append((process, handle))
            deadline = time.monotonic() + float(self.settings["startup_timeout"])
            while process.poll() is None and time.monotonic() < deadline:
                if ready(service, device):
                    row.update(status="ready", reason="모델 서버 시작 완료", owned=True)
                    for task in tasks:
                        if choices[task] == runtime_state.CPU_KEY:
                            choices[task] = service["model_key"]
                    break
                time.sleep(float(self.settings["poll_interval"]))
            else:
                row["reason"] = f"모델 시작 실패 또는 시간 초과; runtime/logs/{name}.log 확인"
                # Retire failed children now, preserving earlier healthy servers.
                owned = self.owned
                self.owned = [owned[-1]]
                self.close()
                self.owned = owned[:-1]
        runtime_state.configure(effective, gpu, choices, records)
        return dict(runtime_state.STATE)


def main(app):
    parser = argparse.ArgumentParser(description="TS-Lens: automatic GPU serving or CPU-only startup")
    parser.add_argument("--mode", choices=("auto", "cpu", "off", "external"))
    parser.add_argument("--check", action="store_true", help="Print startup decisions without launching servers")
    args = parser.parse_args()
    settings = load_settings()
    mode = args.mode or settings["mode"]
    if mode not in ("auto", "cpu", "off", "external"):
        parser.error("config/runtime.yaml mode must be auto, cpu, off or external")
    if not args.check and importlib.util.find_spec("waitress") is None:
        parser.error("waitress가 없습니다. python -m pip install -r requirements.txt 를 실행하세요.")
    servers = ModelServers(settings)
    def stop(signum, frame):
        raise SystemExit(128 + signum)
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        report = servers.prepare(mode, check=args.check)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        if not args.check:
            from waitress import serve
            serve(app, host=cfg("app.host"), port=int(cfg("app.port")), threads=4)
    finally:
        servers.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
