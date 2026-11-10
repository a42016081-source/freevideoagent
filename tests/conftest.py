"""Test setup: every test gets its own throw-away skills/state/output folders and no network."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from agent.config import settings
    monkeypatch.setattr(settings, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(settings, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(settings, "ALLOWED_USER_IDS", [111])
    monkeypatch.setattr(settings, "RENDER_OFFLINE", True)
    monkeypatch.setattr(settings, "GITHUB_REPO", "")
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "")
    for k in ("CLOUDFLARE_ACCOUNT_ID", "D1_DATABASE_ID", "CLOUDFLARE_API_TOKEN"):
        monkeypatch.setattr(settings, k, "")
    for d in ("skills", "state", "output"):
        (tmp_path / d).mkdir()
    return tmp_path


class FakeTG:
    """Records what the bot would send to Telegram."""

    def __init__(self):
        self.sent, self.edited, self.photos, self.answers = [], [], [], []
        self.files = {}

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return {"message_id": len(self.sent)}

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edited.append((chat_id, message_id, text))

    def send_photo(self, chat_id, photo, caption=""):
        self.photos.append((chat_id, photo, caption))

    def answer_callback(self, cid, text=""):
        self.answers.append((cid, text))

    def download_file(self, file_id, max_bytes=5_000_000):
        return self.files[file_id]

    @property
    def last(self):
        return self.sent[-1][1] if self.sent else ""


@pytest.fixture
def tg():
    return FakeTG()


def msg(text, uid=111, chat=111, **extra):
    return {"update_id": 1, "message": {"message_id": 5, "from": {"id": uid}, "chat": {"id": chat},
                                        "text": text, **extra}}
