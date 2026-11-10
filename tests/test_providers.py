"""NVIDIA / OpenAI-style providers and the Telegram client, tested against a local fake server."""
import base64
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PIL import Image

from agent.config import settings
from agent.providers import image as image_mod
from agent.providers import llm
from bot.tg import Telegram, TelegramError

seen = {}


def _png_b64():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 200, 30)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        seen.update(path=self.path, auth=self.headers.get("Authorization"), body=body)
        if self.path.endswith("/chat/completions"):
            out = {"choices": [{"message": {"content": "namaste from fake nvidia"}}]}
        elif "artifacts" in self.path:
            out = {"artifacts": [{"base64": _png_b64()}]}
        elif "boom" in self.path:
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        else:
            out = {"image": _png_b64()}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_nvidia_chat_uses_bearer_key_and_model(server, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setattr(settings, "NVIDIA_BASE_URL", server + "/v1")
    monkeypatch.setenv("LLM_ORDER", "nvidia")
    text, provider = llm.complete_ex("sys", "hi", timeout=10)
    assert provider == "nvidia" and "fake nvidia" in text
    assert seen["auth"] == "Bearer nvapi-test" and seen["path"] == "/v1/chat/completions"
    assert seen["body"]["model"] == settings.NVIDIA_MODEL
    assert seen["body"]["messages"][0]["role"] == "system"


def test_without_a_key_nvidia_is_skipped(monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")
    monkeypatch.setenv("LLM_ORDER", "nvidia")
    with pytest.raises(llm.LLMUnavailable):
        llm.complete_ex("s", "u", timeout=5)


@pytest.mark.parametrize("model,path_hint", [("black-forest-labs/flux.1-schnell", "flux"),
                                              ("stabilityai/stable-diffusion-3-medium", "diffusion")])
def test_nvidia_image_variants(server, monkeypatch, model, path_hint):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setattr(settings, "NVIDIA_IMAGE_URL", server + "/v1/genai")
    monkeypatch.setattr(settings, "NVIDIA_IMAGE_MODEL", model)
    img = image_mod.nvidia_image("a red fort at dawn", 1080, 1920)
    assert img is not None and img.size == (64, 64)
    assert seen["auth"] == "Bearer nvapi-test" and path_hint in seen["path"]
    assert "aspect_ratio" in seen["body"] or "width" in seen["body"]


def test_nvidia_image_artifacts_format_and_failure(server, monkeypatch):
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setattr(settings, "NVIDIA_IMAGE_URL", server + "/v1/genai")
    monkeypatch.setattr(settings, "NVIDIA_IMAGE_MODEL", "x/artifacts")
    assert image_mod.nvidia_image("p", 1024, 1024) is not None
    monkeypatch.setattr(settings, "NVIDIA_IMAGE_MODEL", "x/boom")
    assert image_mod.nvidia_image("p", 1024, 1024) is None          # failure returns None, never raises


def test_telegram_errors_never_contain_the_bot_token():
    tg = Telegram("123456:SECRET-TOKEN")
    tg.base = "http://127.0.0.1:9/bot123456:SECRET-TOKEN"            # nothing listens here
    with pytest.raises(TelegramError) as e:
        tg.call("sendMessage", {"chat_id": 1, "text": "x"}, retries=0)
    assert "SECRET-TOKEN" not in str(e.value)
