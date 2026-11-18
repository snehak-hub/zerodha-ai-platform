import time
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import JWT_ALGORITHM, JWT_EXPIRE_MINUTES, JWT_SECRET
from backend.db.database import get_db
from backend.db.models import User

bearer_scheme = HTTPBearer(auto_error=False)
# Pre-computed hash so login takes the same time whether or not the email exists
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode())
    except ValueError:
        return False


def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES)},
                      JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
                     db: Session = Depends(get_db)) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated",
                                 headers={"WWW-Authenticate": "Bearer"})
    if creds is None:
        raise unauthorized
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    if not user or not user.is_active:
        raise unauthorized
    return user


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email.lower()))
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    return user if (user and ok and user.is_active) else None


# --- Minimal brute-force protection (per process; nginx limit_req covers multi-worker setups) ---
_FAILS: dict[str, deque] = defaultdict(deque)
_LOCK = threading.Lock()
MAX_FAILS, WINDOW_S = 8, 15 * 60


def check_login_allowed(key: str) -> None:
    now = time.monotonic()
    with _LOCK:
        q = _FAILS[key]
        while q and now - q[0] > WINDOW_S:
            q.popleft()
        if len(q) >= MAX_FAILS:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again in 15 minutes.")


def record_login_failure(key: str) -> None:
    with _LOCK:
        _FAILS[key].append(time.monotonic())


def clear_login_failures(key: str) -> None:
    with _LOCK:
        _FAILS.pop(key, None)
