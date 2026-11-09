"""Pinned offline CPU model, ephemeral authenticated loopback inference only."""

from contextlib import AbstractContextManager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from urllib.error import URLError, HTTPError
from urllib.request import Request, build_opener, ProxyHandler
import uuid
import jsonschema

from research.fetch_prices import NoRedirect
from research.local_files import exclusive_lock
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest
from research.rights import as_utc

BASE = Path(__file__).resolve().parent


class Deferred(RuntimeError): pass


def read(path): return json.loads(Path(path).read_text(encoding="utf-8"))


def policy():
    value = read(BASE / "local_ai_policy.json")
    if value["monthly_cost_usd"] != 0 or value["external_inference_enabled"] or value["model_training_enabled"] or value["released_scores_enabled"] or value["host"] != "127.0.0.1":
        raise ValueError("LOCAL_INFERENCE_BOUNDARY_CHANGED")
    if not 1 <= value["cpu_threads"] <= 2 or value["context_tokens"] > 4096 or value["maximum_output_tokens"] > 320 or value["maximum_calls_per_server"] > 24:
        raise ValueError("LOCAL_RESOURCE_BUDGET_CHANGED")
    return value


def available_memory():
    if os.name != "nt": return 0
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in ("total_physical", "available_physical", "total_page", "available_page", "total_virtual", "available_virtual", "extended")]
    value = Memory(); value.length = ctypes.sizeof(value)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)): return 0
    return value.available_physical


def verified_artifacts(settings=None):
    settings = settings or policy()
    path = (ROOT / settings["provision_manifest"]).resolve()
    if not path.is_relative_to((ROOT / "reports/private").resolve()) or digest(path) != settings["provision_manifest_sha256"]: raise ValueError("LOCAL_PROVISION_RECEIPT_CHANGED")
    receipt = read(path); root = Path(receipt["root"]).resolve()
    if not root.is_relative_to((ROOT / "local_data").resolve()) or receipt["model_sha256"] != settings["model_sha256"] or receipt["model_revision"] != settings["model_revision"] or receipt["runtime_tag"] != settings["runtime_tag"]:
        raise ValueError("LOCAL_MODEL_IDENTITY_CHANGED")
    model = Path(receipt["model_path"]).resolve()
    if not model.is_relative_to(root) or model.stat().st_size != receipt["model_bytes"] or digest(model) != settings["model_sha256"]: raise ValueError("LOCAL_MODEL_BYTES_CHANGED")
    for name, sha in receipt["runtime_files"].items():
        target = (root / name).resolve()
        if not target.is_relative_to(root) or digest(target) != sha: raise ValueError("LOCAL_RUNTIME_BYTES_CHANGED")
    executable = root / settings["runtime_tag"] / "llama-server.exe"
    if executable.relative_to(root).as_posix() not in receipt["runtime_files"]: raise ValueError("LOCAL_SERVER_NOT_IN_RECEIPT")
    return receipt, executable


def clean_environment():
    result = {name:os.environ[name] for name in ("SystemRoot", "WINDIR", "PATH", "COMSPEC", "TEMP", "TMP", "PATHEXT") if name in os.environ}
    result["OMP_NUM_THREADS"] = "2"
    return result


class Server(AbstractContextManager):
    def __init__(self, *, root=None, settings=None, popen=subprocess.Popen, opener=None, memory=available_memory, pause=time.sleep, monotonic=time.monotonic, verifier=verified_artifacts):
        self.settings = settings or policy()
        self.root = Path(root or ROOT / "reports/private/local_inference_sessions").resolve()
        if not self.root.is_relative_to((ROOT / "reports/private").resolve()): raise ValueError("PRIVATE_INFERENCE_SESSION_REQUIRED")
        self.popen, self.opener, self.memory, self.pause, self.monotonic, self.verifier = popen, opener or build_opener(ProxyHandler({}), NoRedirect()), memory, pause, monotonic, verifier
        self.calls = self.prompt_tokens = self.completion_tokens = 0
        self.process = self.log = self.lock = None

    def __enter__(self):
        if not as_utc(self.settings["frozen_at"]) <= as_utc(now()) < as_utc(self.settings["review_due_at"]): raise Deferred("LOCAL_POLICY_REVIEW_EXPIRED")
        if self.memory() < self.settings["minimum_available_memory_bytes"]: raise Deferred("LOCAL_MODEL_WAITING_FOR_MEMORY")
        self.lock = exclusive_lock(ROOT / "local_data/local_inference/runtime.lock")
        self.lock.__enter__()
        try:
            receipt, exe = self.verifier(self.settings)
            self.folder = self.root / uuid.uuid4().hex; self.folder.mkdir(parents=True, exist_ok=False)
            self.secret = secrets.token_urlsafe(32); self.key_file = self.folder / "session.key"; self.key_file.write_text(self.secret, encoding="ascii")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
            self.base = "http://127.0.0.1:" + str(port)
            args = [str(exe), "-m", receipt["model_path"], "--host", "127.0.0.1", "--port", str(port), "--api-key-file", str(self.key_file),
                "--ctx-size", str(self.settings["context_tokens"]), "--threads", "2", "--threads-batch", "2", "--parallel", "1", "--n-gpu-layers", "0", "--fit", "off",
                "--no-webui", "--no-ui-mcp-proxy", "--no-slots", "--chat-template-kwargs", '{"enable_thinking":false}', "--prio", "-1", "--poll", "0"]
            self.log = (self.folder / "runtime.log").open("wb")
            self.process = self.popen(args, cwd=exe.parent, env=clean_environment(), stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            started = self.monotonic()
            while self.monotonic() - started < self.settings["startup_timeout_seconds"]:
                if self.process.poll() is not None: raise Deferred("LOCAL_MODEL_START_FAILED")
                try:
                    response = self._request("/health", None, timeout=2)
                    if response.get("status") == "ok": return self
                except (OSError, ValueError): pass
                self.pause(.25)
            raise Deferred("LOCAL_MODEL_STARTUP_TIMEOUT")
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _request(self, route, payload, *, timeout):
        if route not in {"/health", "/v1/chat/completions"}: raise ValueError("UNSUPPORTED_LOCAL_ROUTE")
        raw = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request = Request(self.base + route, data=raw, headers={"Authorization":"Bearer " + self.secret, "Content-Type":"application/json"})
        with self.opener.open(request, timeout=timeout) as response:
            data = response.read(self.settings["maximum_response_bytes"] + 1)
        if len(data) > self.settings["maximum_response_bytes"]: raise ValueError("LOCAL_RESPONSE_SIZE_CAP")
        return json.loads(data)

    def ask(self, messages, schema):
        if self.calls >= self.settings["maximum_calls_per_server"]: raise Deferred("LOCAL_MODEL_CALL_CAP")
        if any(m.get("role") not in {"system", "user"} or not isinstance(m.get("content"), str) for m in messages) or len(json.dumps(messages, ensure_ascii=False).encode()) > self.settings["maximum_input_bytes"]:
            raise ValueError("LOCAL_PROMPT_SCOPE_OR_SIZE_INVALID")
        self.calls += 1
        payload = {"model":self.settings["model_id"], "messages":messages, "temperature":self.settings["temperature"], "seed":self.settings["seed"],
            "top_p":0.8, "top_k":20, "min_p":0,
            "max_tokens":self.settings["maximum_output_tokens"], "stream":False, "chat_template_kwargs":{"enable_thinking":False},
            "response_format":{"type":"json_schema", "json_schema":{"name":"paragraph_classification", "strict":True, "schema":schema}}}
        try: result = self._request("/v1/chat/completions", payload, timeout=self.settings["request_timeout_seconds"])
        except OSError: raise Deferred("LOCAL_INFERENCE_TRANSIENT_FAILURE") from None
        usage = result.get("usage", {})
        self.prompt_tokens += int(usage.get("prompt_tokens", 0)); self.completion_tokens += int(usage.get("completion_tokens", 0))
        choice = result["choices"][0]
        if choice.get("finish_reason") != "stop": raise ValueError("LOCAL_OUTPUT_TRUNCATED")
        value = json.loads(choice["message"]["content"])
        jsonschema.Draft202012Validator(schema).validate(value)
        return value, {"prompt_tokens":int(usage.get("prompt_tokens", 0)), "completion_tokens":int(usage.get("completion_tokens", 0)), "inference":"local_cpu", "external_calls":0}

    def __exit__(self, *exc):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=10)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=5)
        if self.log is not None: self.log.close()
        if hasattr(self, "key_file"): self.key_file.unlink(missing_ok=True)
        if self.lock is not None:
            self.lock.__exit__(None, None, None); self.lock = None
        return False
