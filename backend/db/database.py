from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool

from backend.config import DATABASE_URL

_kwargs = {"pool_pre_ping": True}  # transparently recovers from dropped connections
if DATABASE_URL.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}
    if ":memory:" in DATABASE_URL:
        _kwargs["poolclass"] = StaticPool
else:
    _kwargs.update(pool_size=5, max_overflow=10, pool_recycle=1800)

engine = create_engine(DATABASE_URL, **_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def get_db():
    """FastAPI dependency: one DB session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
