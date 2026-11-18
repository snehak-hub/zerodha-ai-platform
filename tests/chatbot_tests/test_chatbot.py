from fastapi.testclient import TestClient
from tests.helpers import make_authed_client
from ai_workflows.chatbot import chat_service

client = make_authed_client()


def _offline(monkeypatch):
    monkeypatch.setattr(chat_service, "_load_env_key", lambda: None)
    monkeypatch.setattr(chat_service, "execute_tool",
                        lambda *a, **k: {"result": {"live_news": []}})


def test_chat_uses_portfolio_numbers(monkeypatch):
    _offline(monkeypatch)
    r = client.post("/api/chat", json={"message": "What is my portfolio value and pnl?",
                                       "portfolio_id": "port_diversified_01"})
    assert r.status_code == 200
    body = r.json()
    assert body["portfolio_loaded"] is True
    assert "619,700" in body["answer"]


def test_chat_news_falls_back_to_sample(monkeypatch):
    _offline(monkeypatch)
    r = client.post("/api/chat", json={"message": "any latest news on my portfolio?",
                                       "portfolio_id": "port_diversified_01"})
    assert r.status_code == 200
    assert r.json()["news"]


def test_chat_without_portfolio_still_answers(monkeypatch):
    _offline(monkeypatch)
    r = client.post("/api/chat", json={"message": "what is diversification?"})
    assert r.status_code == 200 and r.json()["answer"]


def test_compliance_filter_blocks_advice():
    out = chat_service._sanitize("You must buy this, it is a multibagger")
    assert "can't provide buy/sell" in out


def test_empty_message_rejected():
    assert client.post("/api/chat", json={"message": "  "}).status_code == 400


def test_live_news_never_raises(monkeypatch):
    import httpx
    from mcp_server.tools import live_news_tools as t
    def boom(*a, **k): raise httpx.ConnectError("offline")
    monkeypatch.setattr(t.httpx, "get", boom)
    assert t.fetch_live_news("nifty")["live_news"] == []


# ---------------- Gemini path: success, failures, retries, history ----------------
import time
import google.genai as genai
from tests.helpers import register_user
from fastapi.testclient import TestClient


class _Resp:
    def __init__(self, text): self.text, self.candidates, self.prompt_feedback = text, [], None


def _fake_gemini(monkeypatch, behaviour):
    """behaviour: list of str (reply) or Exception (raised), consumed one per call."""
    calls = []

    class _Models:
        def generate_content(self, **kw):
            calls.append(kw)
            item = behaviour.pop(0)
            if isinstance(item, Exception):
                raise item
            return _Resp(item)

    class _Client:
        def __init__(self, **kw): self.models = _Models()

    monkeypatch.setattr(genai, "Client", _Client)
    monkeypatch.setattr(chat_service, "_load_env_key", lambda: "AIzaFakeKeyForTests1234567890")
    monkeypatch.setattr(chat_service, "execute_tool", lambda *a, **k: {"result": {"live_news": []}})
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return calls


def test_gemini_success_returns_real_answer(monkeypatch):
    _fake_gemini(monkeypatch, ["Diversification means spreading risk across many assets."])
    r = client.post("/api/chat", json={"message": "what is diversification?", "portfolio_id": "port_diversified_01"}).json()
    assert r["mode"] == "gemini" and r["ai_status"]["ok"] is True
    assert r["answer"].startswith("Diversification means")


def test_invalid_key_is_reported_not_hidden(monkeypatch):
    _fake_gemini(monkeypatch, [Exception("400 INVALID_ARGUMENT. API key not valid. key=AIzaFakeKeyForTests1234567890")])
    r = client.post("/api/chat", json={"message": "hello", "portfolio_id": "port_diversified_01"}).json()
    assert r["mode"] == "rule_based" and r["ai_status"]["code"] == "INVALID_KEY"
    assert "AIzaFake" not in str(r)  # key never leaks


def test_empty_gemini_reply_is_not_labelled_success(monkeypatch):
    _fake_gemini(monkeypatch, [""])
    r = client.post("/api/chat", json={"message": "hello", "portfolio_id": "port_diversified_01"}).json()
    assert r["mode"] == "rule_based" and r["ai_status"]["code"] == "EMPTY"


def test_busy_gemini_is_retried_once(monkeypatch):
    calls = _fake_gemini(monkeypatch, [Exception("503 UNAVAILABLE. The model is overloaded."), "Here is a real answer."])
    r = client.post("/api/chat", json={"message": "explain beta", "portfolio_id": "port_diversified_01"}).json()
    assert r["mode"] == "gemini" and len(calls) == 2


def test_missing_key_reports_no_key(monkeypatch):
    _offline(monkeypatch)
    r = client.post("/api/chat", json={"message": "hello"}).json()
    assert r["ai_status"]["code"] == "NO_KEY"


def test_basic_mode_answers_general_questions_without_portfolio_numbers(monkeypatch):
    _offline(monkeypatch)
    r = client.post("/api/chat", json={"message": "What is diversification?", "portfolio_id": "port_diversified_01"}).json()
    assert "spreading" in r["answer"] and "HHI is" not in r["answer"]
    r = client.post("/api/chat", json={"message": "Is my portfolio too concentrated?", "portfolio_id": "port_diversified_01"}).json()
    assert "HHI is" in r["answer"]


def test_history_sent_to_gemini_is_valid_and_basic_replies_not_saved(monkeypatch):
    calls = _fake_gemini(monkeypatch, ["First real answer.", "Second real answer."])
    c = make_authed_client()
    c.post("/api/chat", json={"message": "first question"})
    c.post("/api/chat", json={"message": "second question"})
    roles = [x.role for x in calls[1]["contents"]]
    assert roles == ["user", "model", "user"]
    # basic-mode (failed AI) replies must not be stored as history
    _offline(monkeypatch)
    c2 = make_authed_client()
    c2.post("/api/chat", json={"message": "hello"})
    assert c2.get("/api/chat/history").json()["messages"] == []


def test_build_contents_fixes_bad_history():
    contents = chat_service._build_contents(
        [{"role": "assistant", "content": "stray"}, {"role": "user", "content": "a"}, {"role": "user", "content": "b"}], "new")
    assert [c.role for c in contents] == ["user"]
