"""Test environment. Runs on SQLite by default; set TEST_DATABASE_URL to run the SAME suite on PostgreSQL:
   TEST_DATABASE_URL=postgresql://zerodha:zerodha_pw@localhost:5432/zerodha_ai_test pytest tests/
Must configure env BEFORE backend modules are imported."""
import os
import tempfile

os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET"] = "test-secret-test-secret-test-secret-123456"
os.environ["GEMINI_API_KEY"] = "mock_test_key"  # forces deterministic offline analysis, never calls the real API
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or \
    f"sqlite:///{os.path.join(tempfile.mkdtemp(), 'test.db')}"

from backend.db.database import Base, engine  # noqa: E402
import backend.db.models  # noqa: E402,F401

Base.metadata.create_all(engine)
