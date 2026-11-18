import time
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.db.database import SessionLocal
from backend.db.models import User, Holding, ChatMessage
from sqlalchemy import select, func
from tests.helpers import make_authed_client, register_user

anon = TestClient(app)

CSV = """Symbol,ISIN,Quantity Available,Quantity Discrepant,Average Price,Previous Closing Price
HDFCBANK,INE040A01034,100,0,1450.00,1520.00
ICICIBANK,INE090A01021,200,0,980.00,1050.00"""


def _wait(client, job_id):
    for _ in range(20):
        d = client.get(f"/api/insights/{job_id}").json()
        if d["status"] in ("COMPLETED", "FAILED"):
            return d
        time.sleep(0.2)
    raise AssertionError("job timeout")


# ---------- authentication ----------
def test_register_login_me_roundtrip():
    email, pw, data = register_user(anon)
    assert data["user"]["email"] == email
    r = anon.post("/api/auth/login", json={"email": email.upper(), "password": pw})  # email is case-insensitive
    assert r.status_code == 200
    me = anon.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.json()["email"] == email


def test_password_is_hashed_in_db():
    email, pw, _ = register_user(anon)
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == email))
        assert u.password_hash != pw and u.password_hash.startswith("$2")


def test_duplicate_email_and_weak_password_rejected():
    email, _, _ = register_user(anon)
    assert anon.post("/api/auth/register", json={"email": email, "password": "Another1234"}).status_code == 409
    assert anon.post("/api/auth/register", json={"email": "x@example.com", "password": "short"}).status_code == 422
    assert anon.post("/api/auth/register", json={"email": "not-an-email", "password": "Another1234"}).status_code == 422


def test_wrong_password_and_unknown_user_get_same_error():
    email, _, _ = register_user(anon)
    a = anon.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
    b = anon.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_brute_force_lockout():
    email, _, _ = register_user(anon)
    codes = [anon.post("/api/auth/login", json={"email": email, "password": f"bad-pass-{i}"}).status_code
             for i in range(10)]
    assert codes[:8] == [401] * 8 and codes[8] == 429


@pytest.mark.parametrize("method,path", [
    ("get", "/api/zerodha/session"), ("post", "/api/chat"), ("get", "/api/operations/metrics"),
    ("post", "/api/portfolio-analysis"), ("get", "/api/insights/job_x"), ("delete", "/api/auth/me"),
])
def test_endpoints_require_token(method, path):
    assert getattr(anon, method)(path).status_code in (401, 403)


def test_invalid_token_rejected():
    assert anon.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


# ---------- data isolation between users ----------
def test_users_have_separate_portfolios():
    alice, bob = make_authed_client(), make_authed_client()
    assert alice.post("/api/zerodha/upload-csv", json={"csv_content": CSV, "client_id": "ALICE"}).status_code == 200
    assert len(alice.get("/api/zerodha/session").json()["holdings"]) == 2
    b = bob.get("/api/zerodha/session").json()
    assert b["is_connected"] is False and b["holdings"] == []


def test_user_cannot_read_another_users_job_or_audit():
    alice, bob = make_authed_client(), make_authed_client()
    job = alice.post("/api/portfolio-analysis", json={"portfolio_id": "port_diversified_01"}).json()["job_id"]
    assert _wait(alice, job)["status"] == "COMPLETED"
    assert bob.get(f"/api/insights/{job}").status_code == 404
    assert bob.get(f"/api/compliance/audit/{job}").status_code == 404
    assert alice.get(f"/api/compliance/audit/{job}").status_code == 200


def test_user_cannot_address_raw_db_portfolio_id():
    alice, bob = make_authed_client(), make_authed_client()
    alice.post("/api/zerodha/upload-csv", json={"csv_content": CSV})
    with SessionLocal() as db:
        alice_id = db.scalar(select(User.id).where(User.email == alice.get("/api/auth/me").json()["email"]))
    r = bob.post("/api/portfolio-analysis", json={"portfolio_id": f"user:{alice_id}"})
    assert r.status_code == 403
    r = bob.post("/api/mcp/tools/execute", json={"tool_name": "get_portfolio_snapshot",
                                                "arguments": {"portfolio_id": f"user:{alice_id}"}})
    assert r.status_code == 403


def test_personal_portfolio_analysis_uses_only_own_holdings():
    alice, bob = make_authed_client(), make_authed_client()
    alice.post("/api/zerodha/upload-csv", json={"csv_content": CSV})
    bob.post("/api/zerodha/login", json={"auth_type": "DEMO", "client_id": "ZR1001"})
    job = alice.post("/api/portfolio-analysis", json={"portfolio_id": "port_user_zerodha"}).json()["job_id"]
    d = _wait(alice, job)
    assert d["status"] == "COMPLETED"
    assert sorted(h["symbol"] for h in d["insights"]["holdings"]) == ["HDFCBANK", "ICICIBANK"]
    assert d["insights"]["portfolio_id"] == "port_user_zerodha"  # internal user id never leaks to the client


def test_chat_history_persists_per_user_and_can_be_cleared(monkeypatch):
    import backend.main as main_module  # simulate a real AI reply (basic-mode replies are intentionally not stored)
    monkeypatch.setattr(main_module, "run_chat", lambda **kw: {"answer": "Spreading money across assets.", "mode": "gemini",
                        "ai_status": {"ok": True}, "news": [], "portfolio_loaded": False, "disclaimer": ""})
    alice, bob = make_authed_client(), make_authed_client()
    alice.post("/api/chat", json={"message": "what is diversification?"})
    hist = alice.get("/api/chat/history").json()["messages"]
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert bob.get("/api/chat/history").json()["messages"] == []
    assert alice.delete("/api/chat/history").status_code == 200
    assert alice.get("/api/chat/history").json()["messages"] == []


def test_delete_account_removes_all_user_data():
    c = make_authed_client()
    c.post("/api/zerodha/upload-csv", json={"csv_content": CSV})
    c.post("/api/chat", json={"message": "hello"})
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == c.get("/api/auth/me").json()["email"]))
    assert c.delete("/api/auth/me").status_code == 200
    with SessionLocal() as db:
        assert db.get(User, uid) is None
        # ON DELETE CASCADE is enforced by PostgreSQL; SQLite needs PRAGMA foreign_keys so only assert on PG
        if db.bind.dialect.name == "postgresql":
            assert db.scalar(select(func.count()).select_from(Holding).where(Holding.user_id == uid)) == 0
            assert db.scalar(select(func.count()).select_from(ChatMessage).where(ChatMessage.user_id == uid)) == 0
    assert c.get("/api/auth/me").status_code == 401


def test_health_reports_database():
    assert anon.get("/health").json()["database"] == "OK"
