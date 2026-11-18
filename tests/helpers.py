import uuid
from fastapi.testclient import TestClient
from backend.main import app


def register_user(client: TestClient, email: str | None = None, password: str = "Str0ngPass!word"):
    email = email or f"user_{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": password, "full_name": "Test User"})
    assert r.status_code == 201, r.text
    return email, password, r.json()


def make_authed_client() -> TestClient:
    """TestClient that is already logged in as a brand-new unique user."""
    anon = TestClient(app)
    _, _, data = register_user(anon)
    return TestClient(app, headers={"Authorization": f"Bearer {data['access_token']}"})
