"""Per-user broker connection + holdings persistence (replaces the old global ZERODHA_SESSION)."""
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.db.models import BrokerConnection, Holding

CORE = {"symbol", "sector", "instrument_id", "quantity", "average_price", "current_price"}


def holding_to_dict(h: Holding) -> Dict[str, Any]:
    d = dict(h.extra or {})
    d.update(symbol=h.symbol, sector=h.sector, instrument_id=h.instrument_id or f"NSE_{h.symbol}",
             quantity=h.quantity, average_price=h.average_price, current_price=h.current_price)
    return d


def get_user_holdings(db: Session, user_id: int) -> List[Dict[str, Any]]:
    rows = db.scalars(select(Holding).where(Holding.user_id == user_id).order_by(Holding.symbol)).all()
    return [holding_to_dict(h) for h in rows]


def replace_user_holdings(db: Session, user_id: int, holdings: List[Dict[str, Any]]) -> None:
    db.execute(delete(Holding).where(Holding.user_id == user_id))
    seen = set()
    for h in holdings:
        sym = str(h.get("symbol", "")).strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        db.add(Holding(
            user_id=user_id, symbol=sym, sector=h.get("sector", "") or "",
            instrument_id=h.get("instrument_id", "") or "",
            quantity=int(float(h.get("quantity", 0))),
            average_price=float(h.get("average_price", 0.0)),
            current_price=float(h.get("current_price", 0.0)),
            extra={k: v for k, v in h.items() if k not in CORE},
        ))
    db.commit()


def save_broker_session(db: Session, user_id: int, session: Dict[str, Any]) -> None:
    """Persists holdings + non-secret connection metadata. Full tokens/passwords/TOTP are never stored."""
    conn = db.scalar(select(BrokerConnection).where(BrokerConnection.user_id == user_id,
                                                    BrokerConnection.broker == "ZERODHA"))
    if not conn:
        conn = BrokerConnection(user_id=user_id, broker="ZERODHA")
        db.add(conn)
    conn.client_id = str(session.get("user_id") or "")
    conn.display_name = session.get("user_name") or ""
    conn.email = session.get("email") or ""
    conn.token_hint = (session.get("access_token") or "")[:40]  # already masked by the login layer
    conn.live_market_synced = bool(session.get("live_market_synced"))
    conn.connected_at = datetime.now(timezone.utc)
    db.commit()
    replace_user_holdings(db, user_id, session.get("holdings") or [])


def load_broker_session(db: Session, user_id: int) -> Dict[str, Any]:
    """Rebuilds the session dict shape the frontend already expects."""
    conn = db.scalar(select(BrokerConnection).where(BrokerConnection.user_id == user_id,
                                                    BrokerConnection.broker == "ZERODHA"))
    if not conn:
        return {"is_connected": False, "user_id": None, "user_name": None, "email": None, "broker": "ZERODHA",
                "access_token": None, "connected_at": None, "holdings": [], "live_market_synced": False}
    return {"is_connected": True, "user_id": conn.client_id, "user_name": conn.display_name, "email": conn.email,
            "broker": conn.broker, "access_token": conn.token_hint,
            "connected_at": conn.connected_at.isoformat() if conn.connected_at else None,
            "holdings": get_user_holdings(db, user_id), "live_market_synced": conn.live_market_synced}


def disconnect_broker(db: Session, user_id: int) -> None:
    db.execute(delete(BrokerConnection).where(BrokerConnection.user_id == user_id))
    db.execute(delete(Holding).where(Holding.user_id == user_id))
    db.commit()
