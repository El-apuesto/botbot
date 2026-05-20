"""
Twin Shadow — LLM Config Override Layer
Persists role→model overrides, prompt overrides, and named templates to llm_config.json.
All admin panel reads/writes flow through this module.
"""
from __future__ import annotations
import json
import threading
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "llm_config.json"
_lock = threading.Lock()

_DEFAULTS: dict = {
    "role_overrides":   {},
    "prompt_overrides": {},
    "templates":        {},
    "ollama_base_url":  "http://localhost:11434",
    "offline_mode":     False,
    "offline_model":    "llama3",
}


def _load() -> dict:
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            for k, v in _DEFAULTS.items():
                data.setdefault(k, v if not isinstance(v, dict) else dict(v))
            return data
        except Exception:
            pass
    return {k: (dict(v) if isinstance(v, dict) else v) for k, v in _DEFAULTS.items()}


def _save(data: dict) -> None:
    _CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_role_override(role: str) -> dict | None:
    with _lock:
        return _load()["role_overrides"].get(role)


def get_prompt_override(role: str) -> str | None:
    with _lock:
        return _load()["prompt_overrides"].get(role)


def set_role(role: str, provider: str, model_key: str) -> None:
    with _lock:
        data = _load()
        data["role_overrides"][role] = {"provider": provider, "model_key": model_key}
        _save(data)


def set_prompt(role: str, prompt: str) -> None:
    with _lock:
        data = _load()
        data["prompt_overrides"][role] = prompt
        _save(data)


def reset_role(role: str) -> None:
    with _lock:
        data = _load()
        data["role_overrides"].pop(role, None)
        _save(data)


def reset_prompt(role: str) -> None:
    with _lock:
        data = _load()
        data["prompt_overrides"].pop(role, None)
        _save(data)


def get_ollama_base_url() -> str:
    with _lock:
        return _load().get("ollama_base_url", "http://localhost:11434")


def set_ollama_base_url(url: str) -> None:
    with _lock:
        data = _load()
        data["ollama_base_url"] = url.rstrip("/")
        _save(data)


def save_template(name: str) -> None:
    with _lock:
        data = _load()
        data["templates"][name] = {
            "role_overrides":   dict(data["role_overrides"]),
            "prompt_overrides": dict(data["prompt_overrides"]),
        }
        _save(data)


def load_template(name: str) -> bool:
    with _lock:
        data = _load()
        tpl = data["templates"].get(name)
        if not tpl:
            return False
        data["role_overrides"]   = dict(tpl.get("role_overrides", {}))
        data["prompt_overrides"] = dict(tpl.get("prompt_overrides", {}))
        _save(data)
        return True


def delete_template(name: str) -> None:
    with _lock:
        data = _load()
        data["templates"].pop(name, None)
        _save(data)


def list_templates() -> list[str]:
    with _lock:
        return list(_load().get("templates", {}).keys())


def get_offline_mode() -> bool:
    with _lock:
        return bool(_load().get("offline_mode", False))


def get_offline_model() -> str:
    with _lock:
        return _load().get("offline_model", "llama3")


def set_offline(enabled: bool, model: str | None = None, base_url: str | None = None) -> None:
    with _lock:
        data = _load()
        data["offline_mode"] = bool(enabled)
        if model is not None:
            data["offline_model"] = model.strip()
        if base_url is not None:
            data["ollama_base_url"] = base_url.rstrip("/")
        _save(data)


def get_full_config() -> dict:
    with _lock:
        return _load()
