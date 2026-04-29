"""
LLM 后端 —— OpenAI 兼容 + Ollama 原生
══════════════════════════════════════════
支持通过 OpenAI 库或直接 REST API 调用 LLM。
"""

import json
import os
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any


class LLMBackend(ABC):
    """LLM 后端抽象基类。"""

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        ...


class OpenAIBackend(LLMBackend):
    """OpenAI 兼容后端（支持 Ollama 的 OpenAI 兼容端点）。"""

    def __init__(self, api_key: str = "", base_url: str = "", model: str = "gpt-3.5-turbo"):
        self.model = model
        self._client = None
        try:
            import openai
            self._client = openai.OpenAI(
                api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
                base_url=base_url or None,
            )
        except ImportError:
            pass

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        if self._client is None:
            return ""
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=256,
            )
            return resp.choices[0].message.content or ""
        except Exception:
            return ""


class OllamaBackend(LLMBackend):
    """Ollama REST API 后端。"""

    def __init__(self, host: str = "http://localhost:11434", model: str = "llama3"):
        self.host = host.rstrip("/")
        self.model = model

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        try:
            import requests
        except ImportError:
            return ""

        url = f"{self.host}/api/chat"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "")
        except Exception:
            return ""


def load_llm_config() -> Optional[Dict[str, Any]]:
    """从 config/llm_config.json 加载 LLM 配置。"""
    config_paths = ["config/llm_config.json", "llm_config.json"]
    for path in config_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return None


def create_backend(config: Optional[Dict[str, Any]] = None) -> Optional[LLMBackend]:
    """根据配置创建 LLM 后端。"""
    if config is None:
        config = load_llm_config()
    if config is None:
        return None

    backend_type = config.get("backend", "openai")
    if backend_type == "openai":
        return OpenAIBackend(
            api_key=config.get("api_key", ""),
            base_url=config.get("base_url", ""),
            model=config.get("model", "gpt-3.5-turbo"),
        )
    elif backend_type == "ollama":
        return OllamaBackend(
            host=config.get("host", "http://localhost:11434"),
            model=config.get("model", "llama3"),
        )
    return None
