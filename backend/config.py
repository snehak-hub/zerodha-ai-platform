"""Central, validated configuration read from environment variables (.env is loaded in main.py)."""
import os
import secrets
import logging

from dotenv import load_dotenv

load_dotenv()  # reads .env from the project root (no-op if absent, e.g. in Docker where env is injected)

log = logging.getLogger("config")


def _normalize_db_url(url: str) -> str:
    """Accept postgres:// and postgresql:// (Render, Heroku, Supabase) and force the psycopg3 driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production"

DATABASE_URL = _normalize_db_url(os.getenv(
    "DATABASE_URL",
    "postgresql://zerodha:zerodha_pw@localhost:5432/zerodha_ai",  # matches docker-compose `db` service
))

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))

_jwt_secret = os.getenv("JWT_SECRET", "")
if not _jwt_secret:
    if IS_PRODUCTION:
        raise RuntimeError("JWT_SECRET must be set when ENVIRONMENT=production "
                           "(generate: python -c \"import secrets;print(secrets.token_urlsafe(48))\")")
    _jwt_secret = secrets.token_urlsafe(48)
    log.warning("JWT_SECRET not set: using a random one. Logins will be invalidated on every restart.")
elif IS_PRODUCTION and len(_jwt_secret) < 32:
    raise RuntimeError("JWT_SECRET must be at least 32 characters in production")
JWT_SECRET = _jwt_secret

CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
if IS_PRODUCTION and "*" in CORS_ORIGINS:
    raise RuntimeError("CORS_ORIGINS must list your real frontend domain(s) in production, not '*'")
