import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.config import CORS_ORIGINS
from backend.db.database import SessionLocal, get_db
from backend.db.models import User
from backend.services import auth_service as auth
from backend.services import holdings_service as holdings_svc
from backend.services import storage_service as store
from backend.services.zerodha_service import (
    authenticate_zerodha_user,
    parse_kite_holdings_csv,
    sync_holdings_with_live_market,
)
from mcp_server.server import execute_tool, get_tool_audit_logs
from ai_workflows.context_builder.builder import build_analysis_context
from ai_workflows.llm_analysis.analyzer import generate_portfolio_analysis
from ai_workflows.validation.validator import validate_analysis_output
from ai_workflows.chatbot.chat_service import chat as run_chat

log = logging.getLogger("api")

app = FastAPI(
    title="Zerodha AI Financial Intelligence Platform API",
    version="2.0.0",
    description="Governed portfolio intelligence, risk analytics and explainable recommendations (multi-user, PostgreSQL).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,  # auth uses Authorization: Bearer headers, not cookies
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

SAMPLE_USER_PORTFOLIO_ALIAS = "port_user_zerodha"


# ----------------- Request Models -----------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(default="", max_length=120)

    @field_validator("password")
    @classmethod
    def _bcrypt_limit(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 bytes")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class PortfolioAnalysisRequest(BaseModel):
    portfolio_id: str
    timeframe: Optional[str] = "1M"


class ToolExecutionRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any]


class FeedbackRequest(BaseModel):
    card_id: str
    user_action: str
    comments: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    portfolio_id: Optional[str] = None
    timeframe: Optional[str] = "1M"


class ZerodhaLoginRequest(BaseModel):
    auth_type: str = "DEMO"
    client_id: Optional[str] = "ZR1001"
    password: Optional[str] = None
    totp: Optional[str] = None
    enctoken: Optional[str] = None
    api_key: Optional[str] = None
    access_token: Optional[str] = None
    request_token: Optional[str] = None
    api_secret: Optional[str] = None


class ZerodhaCsvUploadRequest(BaseModel):
    csv_content: str
    client_id: Optional[str] = "KITE_CSV_USER"


# ----------------- Helpers -----------------
def resolve_portfolio_id(portfolio_id: str, user: User) -> str:
    """Maps the UI alias to the caller's own DB portfolio. Clients may never address another user's portfolio."""
    if portfolio_id == SAMPLE_USER_PORTFOLIO_ALIAS:
        return f"user:{user.id}"
    if portfolio_id.startswith("user:"):
        raise HTTPException(403, "Not allowed")
    return portfolio_id


def _user_out(u: User) -> Dict[str, Any]:
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "created_at": u.created_at.isoformat()}


# ----------------- Async Worker -----------------
def run_analysis_pipeline(job_id: str, resolved_portfolio_id: str, label: str, timeframe: str = "1M"):
    """PENDING -> FETCHING_DATA -> RUNNING_ANALYTICS -> GENERATING_EXPLANATION -> VALIDATING_OUTPUT -> COMPLETED
    Runs in a background task, so it opens its own DB session."""
    with SessionLocal() as db:
        try:
            store.update_job_status(db, job_id, "FETCHING_DATA")
            context = build_analysis_context(resolved_portfolio_id, timeframe)
            context["portfolio_id"] = label

            store.update_job_status(db, job_id, "RUNNING_ANALYTICS")
            store.update_job_status(db, job_id, "GENERATING_EXPLANATION")
            analysis_output = generate_portfolio_analysis(context)

            store.update_job_status(db, job_id, "VALIDATING_OUTPUT")
            is_valid, validation_msg = validate_analysis_output(analysis_output, context)
            if not is_valid:
                raise ValueError(f"Compliance validation failed: {validation_msg}")

            analysis_output["portfolio_id"] = label
            analysis_output["summary_metrics"] = context.get("summary", {})
            analysis_output["risk_metrics"] = context.get("risk_metrics", {})
            analysis_output["holdings"] = context.get("holdings", [])
            analysis_output["sector_analysis"] = context.get("sector_analysis", {})
            analysis_output["benchmark_comparison"] = context.get("benchmark_comparison", {})
            analysis_output["alert_flags"] = context.get("alert_flags", {})
            analysis_output["timeframe"] = timeframe

            store.save_insights(db, job_id, analysis_output)
            store.record_audit(db, job_id, {
                "job_id": job_id,
                "portfolio_id": label,
                "timeframe": timeframe,
                "model_version": "gemini-2.5-flash",
                "prompt_version": "v1.0-explainable-regulated",
                "validation_status": "PASSED",
                "validation_message": validation_msg,
                "context_snapshot": context,
                "output_snapshot": analysis_output,
                "tool_calls": get_tool_audit_logs()[-25:],
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
            store.update_job_status(db, job_id, "COMPLETED")
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.exception("analysis job %s failed", job_id)
            store.update_job_status(db, job_id, "FAILED", error=str(e))


# ----------------- Health -----------------
@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_status = "OK"
    except Exception:  # noqa: BLE001
        raise HTTPException(503, detail={"status": "UNHEALTHY", "database": "DOWN"})
    return {"status": "HEALTHY", "database": db_status, "timestamp": datetime.now(timezone.utc).isoformat()}


# ----------------- Auth -----------------
@app.post("/api/auth/register", status_code=201)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    email = req.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    user = User(email=email, password_hash=auth.hash_password(req.password), full_name=req.full_name.strip())
    db.add(user)
    db.commit()
    return {"access_token": auth.create_access_token(user.id), "token_type": "bearer", "user": _user_out(user)}


@app.post("/api/auth/login")
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    key = f"{(request.client.host if request.client else '?')}:{req.email.lower()}"
    auth.check_login_allowed(key)
    user = auth.authenticate(db, req.email, req.password)
    if not user:
        auth.record_login_failure(key)
        raise HTTPException(401, "Invalid email or password")
    auth.clear_login_failures(key)
    return {"access_token": auth.create_access_token(user.id), "token_type": "bearer", "user": _user_out(user)}


@app.get("/api/auth/me")
def me(user: User = Depends(auth.get_current_user)):
    return _user_out(user)


@app.delete("/api/auth/me")
def delete_account(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    """Deletes the account and ALL its data (holdings, jobs, insights, chat, feedback) via ON DELETE CASCADE."""
    db.delete(user)
    db.commit()
    return {"status": "SUCCESS", "message": "Account and all associated data deleted"}


# ----------------- Analysis -----------------
@app.post("/api/portfolio-analysis")
def initiate_analysis(request: PortfolioAnalysisRequest, background_tasks: BackgroundTasks,
                      user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    timeframe = request.timeframe or "1M"
    resolved = resolve_portfolio_id(request.portfolio_id, user)
    job_id = store.create_job(db, user.id, request.portfolio_id, timeframe)
    background_tasks.add_task(run_analysis_pipeline, job_id, resolved, request.portfolio_id, timeframe)
    return {"job_id": job_id, "status": "PENDING", "portfolio_id": request.portfolio_id, "timeframe": timeframe}


@app.get("/api/insights/{job_id}")
def retrieve_insights(job_id: str, user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    job = store.get_job(db, job_id, user.id)
    if not job:
        raise HTTPException(404, "Analysis job not found")
    return {"job_id": job_id, "status": job.status, "error_message": job.error_message,
            "insights": store.get_insights(db, job_id)}


@app.post("/api/mcp/tools/execute")
def execute_mcp_tool(request: ToolExecutionRequest, user: User = Depends(auth.get_current_user)):
    args = dict(request.arguments)
    if isinstance(args.get("portfolio_id"), str):
        args["portfolio_id"] = resolve_portfolio_id(args["portfolio_id"], user)
    return execute_tool(request.tool_name, args)


@app.post("/api/feedback")
def submit_feedback(request: FeedbackRequest, user: User = Depends(auth.get_current_user),
                    db: Session = Depends(get_db)):
    store.record_feedback(db, user.id, request.card_id, request.user_action, request.comments)
    return {"status": "SUCCESS", "message": "Feedback recorded"}


@app.get("/api/compliance/audit/{job_id}")
def retrieve_audit(job_id: str, user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not store.get_job(db, job_id, user.id):
        raise HTTPException(404, "Audit record not found for this job")
    audit = store.get_audit(db, job_id)
    if not audit:
        raise HTTPException(404, "Audit record not found for this job")
    return audit


@app.get("/api/operations/metrics")
def retrieve_ops_metrics(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    c = store.job_counts(db, user.id)
    return {
        "total_jobs": c["total"],
        "completed_jobs": c["completed"],
        "failed_jobs": c["failed"],
        "success_rate_pct": round(c["completed"] / c["total"] * 100, 2) if c["total"] else 100.0,
        "total_feedback_entries": store.feedback_count(db, user.id),
        "recent_tool_calls": len(get_tool_audit_logs()),
    }


# ----------------- AI Chatbot -----------------
@app.post("/api/chat")
def chat_endpoint(req: ChatRequest, user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "Message cannot be empty")
    message = message[:2000]
    portfolio_id = resolve_portfolio_id(req.portfolio_id, user) if req.portfolio_id else None
    history = store.recent_chat(db, user.id, 10)  # server-side memory, never trusted from the client
    result = run_chat(message=message, portfolio_id=portfolio_id, timeframe=req.timeframe or "1M", history=history)
    if result["mode"] == "gemini":  # basic-mode replies are not saved: they would pollute the AI's context
        store.add_chat_message(db, user.id, "user", message)
        store.add_chat_message(db, user.id, "assistant", result["answer"])
    return result


@app.get("/api/chat/history")
def chat_history(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return {"messages": store.recent_chat(db, user.id, 50)}


@app.delete("/api/chat/history")
def clear_chat_history(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    store.clear_chat(db, user.id)
    return {"status": "SUCCESS"}


# ----------------- Zerodha (per user) -----------------
@app.post("/api/zerodha/login")
def zerodha_login(req: ZerodhaLoginRequest, user: User = Depends(auth.get_current_user),
                  db: Session = Depends(get_db)):
    try:
        session = authenticate_zerodha_user(
            auth_type=req.auth_type, client_id=req.client_id, password=req.password, totp=req.totp,
            enctoken=req.enctoken, api_key=req.api_key, access_token=req.access_token,
            request_token=req.request_token, api_secret=req.api_secret,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    holdings_svc.save_broker_session(db, user.id, session)
    return {"status": "SUCCESS", "session": holdings_svc.load_broker_session(db, user.id)}


@app.get("/api/zerodha/session")
def get_zerodha_session(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return holdings_svc.load_broker_session(db, user.id)


@app.post("/api/zerodha/sync-live-market")
def sync_zerodha_live_market(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    session = holdings_svc.load_broker_session(db, user.id)
    if not session["is_connected"]:
        raise HTTPException(400, "No active Zerodha portfolio connected")
    if session["holdings"]:
        holdings_svc.replace_user_holdings(db, user.id, sync_holdings_with_live_market(session["holdings"]))
    return {"status": "SUCCESS", "session": holdings_svc.load_broker_session(db, user.id)}


@app.post("/api/zerodha/upload-csv")
def upload_kite_csv(req: ZerodhaCsvUploadRequest, user: User = Depends(auth.get_current_user),
                    db: Session = Depends(get_db)):
    try:
        holdings = parse_kite_holdings_csv(req.csv_content)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e))
    cid = req.client_id or "KITE_CSV_USER"
    holdings_svc.save_broker_session(db, user.id, {
        "user_id": cid, "user_name": f"Kite Investor ({cid})", "email": "investor@kite.statement",
        "access_token": "csv_session", "live_market_synced": True, "holdings": holdings,
    })
    return {"status": "SUCCESS", "session": holdings_svc.load_broker_session(db, user.id)}


@app.post("/api/zerodha/logout")
def zerodha_logout(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    holdings_svc.disconnect_broker(db, user.id)
    return {"status": "SUCCESS", "message": "Zerodha session disconnected"}
