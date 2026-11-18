"""
Portfolio chatbot.

Answers user questions using three grounded sources:
  1. The user's portfolio + deterministic analytics (computed in Python, never by the LLM)
  2. Latest news (live RSS via MCP tool, falling back to the bundled sample news CSV)
  3. The LLM's general financial knowledge, for non-portfolio questions

Falls back to a rule-based answerer if GEMINI_API_KEY is missing or the API call fails,
so the chat always works offline.
"""
import os
import re
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from ai_workflows.context_builder.builder import build_analysis_context
from mcp_server.server import execute_tool

DISCLAIMER = ("This is educational information generated from your portfolio data, "
              "not SEBI-registered investment advice.")

SYSTEM_PROMPT = """You are a friendly, knowledgeable Indian-markets portfolio assistant inside the Zerodha AI Financial Intelligence Platform.

You receive a JSON block called PORTFOLIO_CONTEXT (the user's holdings and analytics, already computed by Python) and NEWS (latest headlines).

RULES
1. Portfolio questions: answer ONLY from PORTFOLIO_CONTEXT. Quote the exact numbers given (weights, P&L, HHI, beta, volatility, drawdown). NEVER do your own arithmetic or invent figures; if a number is not in the context, say it is not available.
2. News questions: use NEWS. Mention the source name and say which holding it relates to. If NEWS is empty, say live news could not be fetched.
3. General finance questions (what is HHI, what is diversification, how do SIPs work, how does the market work...): answer fully and helpfully from your own knowledge, like a knowledgeable friend: simple language, a short example, and the key point first. Do NOT force the user's portfolio numbers into a general answer; only connect it to their portfolio at the end if that genuinely helps.
4. You are NOT a SEBI-registered adviser. Never tell the user to buy, sell or hold a specific stock, never give target prices, never promise returns. Instead explain risks, trade-offs and what the data shows, and suggest they consult a SEBI-registered adviser for decisions.
5. Write natural, conversational answers in short paragraphs (bullets only when listing). Never answer with just a number or a template. Use ₹ for currency. Reply in the language the user writes in (English, Hindi or Hinglish)."""

PROHIBITED = ["guaranteed return", "guaranteed profit", "100% safe", "must buy", "must sell",
              "buy immediately", "sell immediately", "target price", "multibagger"]


# ---------------------------------------------------------------- context
def _load_env_key() -> Optional[str]:
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY=") and not line.startswith("#"):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if key:
                        os.environ["GEMINI_API_KEY"] = key
                    return key
    return None


def _mentioned_symbols(message: str, holdings: List[Dict[str, Any]]) -> List[str]:
    text = message.upper()
    return [h["symbol"] for h in holdings if re.search(rf"\b{re.escape(h['symbol'].upper())}\b", text)]


def gather_news(message: str, context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Live news first (MCP tool), sample CSV news as offline fallback."""
    holdings = (context or {}).get("holdings", [])
    mentioned = _mentioned_symbols(message, holdings)
    if mentioned:
        query = f"{mentioned[0]} share NSE"
    elif holdings and re.search(r"portfolio|holding|my stock|my share|news", message, re.I):
        top = sorted(holdings, key=lambda h: h.get("weight_pct", 0), reverse=True)[:3]
        query = " OR ".join(f"{h['symbol']} share" for h in top)
    else:
        query = "Indian stock market Nifty Sensex"

    live = execute_tool("fetch_live_news", {"query": query, "max_items": 5})
    items = (live.get("result") or {}).get("live_news", [])
    if items:
        return [{"headline": i["headline"], "source": i["source"], "published_at": i["published_at"]}
                for i in items]

    # Offline fallback: bundled sample signals already collected by the context builder
    return [{"headline": n["headline"], "source": "sample data", "published_at": n["published_at"],
             "symbol": n["symbol"], "impact": n["impact_tag"]}
            for n in (context or {}).get("news_signals", [])[:5]]


def _compact_context(ctx: Dict[str, Any]) -> Dict[str, Any]:
    keep = ["summary", "risk_metrics", "benchmark_comparison", "alert_flags", "sector_analysis", "top_movers"]
    out = {k: ctx.get(k) for k in keep}
    out["holdings"] = [
        {k: h.get(k) for k in ("symbol", "sector", "quantity", "average_price", "current_price",
                               "current_value", "unrealized_pnl", "weight_pct", "pnl_contribution_pct")}
        for h in ctx.get("holdings", [])
    ]
    out["data_freshness"] = ctx.get("data_freshness")
    return out


# ---------------------------------------------------------------- errors
def _scrub(text: str) -> str:
    """Never let an API key leak into logs or responses."""
    return re.sub(r"(AIza[\w-]{10,}|AQ\.[\w.-]{10,})", "[hidden-key]", text or "")


def _friendly_error(raw: str) -> Dict[str, str]:
    r = (raw or "").lower()
    if "api key not valid" in r or "api_key_invalid" in r or "invalid api key" in r:
        return {"code": "INVALID_KEY", "message": "Gemini rejected the API key. Create a new key in Google AI Studio and update GEMINI_API_KEY in .env."}
    if "404" in r or "not_found" in r or "not found" in r:
        return {"code": "MODEL_NOT_FOUND", "message": "The Gemini model name was not found. Check LLM_MODEL in .env (for example gemini-2.5-flash)."}
    if "429" in r or "resource_exhausted" in r or "quota" in r:
        return {"code": "QUOTA", "message": "Gemini free-tier limit reached. Wait a minute and try again."}
    if "403" in r or "permission" in r:
        return {"code": "PERMISSION", "message": "This API key is not allowed to use the Gemini API. Check the key's project/restrictions in Google AI Studio."}
    if "503" in r or "unavailable" in r or "overloaded" in r:
        return {"code": "BUSY", "message": "Gemini is busy right now. Please try again in a moment."}
    if "timeout" in r or "timed out" in r or "connect" in r or "name or service" in r:
        return {"code": "NETWORK", "message": "Could not reach Gemini. Check your internet connection."}
    return {"code": "ERROR", "message": "The AI service returned an error. See the backend terminal for details."}


# ---------------------------------------------------------------- Gemini call
def _build_contents(history: Optional[List[Dict[str, str]]], final_user_text: str):
    """Valid Gemini conversation: starts with a user turn, roles alternate, last turn is the new question."""
    from google.genai import types

    turns: List[List[str]] = []
    for t in (history or [])[-10:]:
        role = "user" if t.get("role") == "user" else "model"
        text = (t.get("content") or "").strip()
        if not text:
            continue
        if turns and turns[-1][0] == role:
            turns[-1][1] += "\n" + text
        else:
            turns.append([role, text])
    while turns and turns[0][0] != "user":
        turns.pop(0)
    if turns and turns[-1][0] == "user":
        turns[-1][1] += "\n" + final_user_text
    else:
        turns.append(["user", final_user_text])
    return [types.Content(role=r, parts=[types.Part(text=t)]) for r, t in turns]


def _call_gemini(api_key: str, contents) -> Tuple[Optional[str], Optional[Dict[str, str]]]:
    """Returns (answer, None) on success or (None, {code, message}) on failure. One retry if Gemini is busy."""
    from google import genai
    from google.genai import types

    model = os.environ.get("LLM_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=30000))
    last_raw = ""
    for attempt in range(2):
        try:
            resp = client.models.generate_content(
                model=model, contents=contents,
                config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, temperature=0.3),
            )
            text = (resp.text or "").strip() if resp is not None else ""
            if text:
                return text, None
            cands = getattr(resp, "candidates", None) or []
            reason = getattr(cands[0], "finish_reason", None) if cands else None
            block = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
            print(f"[Warning] Gemini returned an empty reply (finish_reason={reason}, block_reason={block})")
            return None, {"code": "EMPTY", "message": f"Gemini returned an empty reply ({reason or block or 'no reason given'}). Try rephrasing your question."}
        except Exception as err:  # noqa: BLE001
            last_raw = _scrub(str(err))
            print(f"[Warning] Chat LLM call failed (attempt {attempt + 1}): {last_raw}")
            if attempt == 0 and any(k in last_raw for k in ("503", "UNAVAILABLE", "overloaded")):
                time.sleep(1.0)
                continue
            break
    return None, _friendly_error(last_raw)


# ---------------------------------------------------------------- offline (basic mode) answers
GLOSSARY = [
    (r"\bhhi\b|herfindahl", "HHI (Herfindahl-Hirschman Index) measures how concentrated a portfolio is: square each holding's % weight and add them up. Below about 1,500 is well diversified, 1,500 to 2,500 is moderate, and above 2,500 is highly concentrated."),
    (r"\bbeta\b", "Beta shows how strongly a stock or portfolio moves compared with the market (for example the Nifty 50). Beta 1 moves with the market, above 1 swings more than the market, below 1 swings less."),
    (r"volatil", "Volatility is how much returns swing up and down, usually shown as annualised standard deviation. Higher volatility means a bumpier ride, not automatically worse returns."),
    (r"drawdown", "Maximum drawdown is the biggest fall from a peak to a later low. It shows the worst loss an investor would have had to sit through."),
    (r"diversif", "Diversification means spreading your money across different stocks, sectors and asset types so one bad outcome does not hurt everything. Example: holding banks, IT, pharma and energy rather than only banks."),
    (r"\bsips?\b|systematic investment", "A SIP (Systematic Investment Plan) invests a fixed amount at regular intervals, for example Rs 5,000 every month, into a mutual fund. It builds a habit and averages your purchase price over time (rupee-cost averaging)."),
    (r"\bnifty\b", "The Nifty 50 is the NSE index of 50 large Indian companies and is a common benchmark for the Indian market."),
    (r"\bsensex\b", "The Sensex is the BSE index of 30 large, established Indian companies."),
    (r"p&l|pnl|unrealized|unrealised", "Unrealized P&L is the gain or loss on shares you still hold: (current price minus average buy price) times quantity. It becomes realized only when you sell."),
    (r"mutual fund", "A mutual fund pools money from many investors and a fund manager invests it in stocks, bonds or both. You own units whose price (NAV) moves with the fund's holdings."),
    (r"\betf\b", "An ETF (exchange-traded fund) tracks an index or asset and trades on the stock exchange like a share, usually with low costs."),
]
PERSONAL = re.compile(r"\b(my|mine|i|me|portfolio|holdings?|invested|stocks?|shares?)\b", re.I)


def _rule_based_answer(message: str, ctx: Optional[Dict[str, Any]], news: List[Dict[str, Any]]) -> str:
    """Offline fallback: answers from computed analytics, or a short glossary for general terms."""
    m = message.lower()
    mentions_holding = bool(ctx and _mentioned_symbols(message, ctx.get("holdings", [])))
    is_personal = bool(PERSONAL.search(message)) or mentions_holding

    if re.search(r"news|headline|latest|happening", m):
        if not news:
            return "I couldn't fetch news right now. Please try again in a moment."
        return "Latest headlines relevant to your query:\n" + "\n".join(f"• {n['headline']} ({n['source']})" for n in news)

    if not is_personal:
        for pattern, text in GLOSSARY:
            if re.search(pattern, m):
                return text
        return ("I can only answer general market questions when the AI model is connected. "
                "Meanwhile I can answer questions about your portfolio: value, P&L, risk, sectors, best/worst stock and news.")

    if not ctx or not ctx.get("holdings"):
        return ("I can't see any holdings for the selected portfolio yet. Pick a sample portfolio or connect "
                "Zerodha, then ask me again.")
    s, r, hs = ctx.get("summary", {}), ctx.get("risk_metrics", {}), ctx["holdings"]
    sec = ctx.get("sector_analysis", {})

    if re.search(r"worst|loss|lagging|underperform", m):
        w = ctx.get("top_movers", {}).get("worst_performer", {})
        return f"Your weakest holding is {w.get('symbol')} with an unrealized P&L of ₹{w.get('unrealized_pnl'):,}." if w else "No loss data available."
    if re.search(r"best|top performer|gain|profit", m):
        b = ctx.get("top_movers", {}).get("best_performer", {})
        return f"Your best performer is {b.get('symbol')} with an unrealized P&L of ₹{b.get('unrealized_pnl'):,}." if b else "No gain data available."
    if re.search(r"sector|diversif|concentrat|hhi", m):
        return (f"HHI is {s.get('hhi_index')} ({s.get('concentration_status')} concentration). "
                f"Dominant sector: {sec.get('dominant_sector', 'n/a')}. Lower HHI means a more spread-out portfolio.")
    if re.search(r"risk|volatil|beta|drawdown", m):
        return "Risk snapshot: " + ", ".join(f"{k.replace('_', ' ')} = {v}" for k, v in r.items() if isinstance(v, (int, float, str))) + "."
    if re.search(r"value|worth|invested|pnl|p&l|return", m):
        return (f"Invested ₹{s.get('total_invested'):,}, current value ₹{s.get('total_current_value'):,}, "
                f"unrealized P&L ₹{s.get('total_unrealized_pnl'):,} ({s.get('total_pnl_pct')}%).")
    return (f"You hold {len(hs)} stocks worth ₹{s.get('total_current_value'):,} "
            f"(P&L {s.get('total_pnl_pct')}%, HHI {s.get('hhi_index')}). Ask me about risk, sectors, best/worst stocks, or news.")


def _sanitize(text: str) -> str:
    low = text.lower()
    if any(p in low for p in PROHIBITED):
        return ("I can't provide buy/sell instructions, price targets or return guarantees. "
                "I can explain the risks and data behind your holdings instead. " + DISCLAIMER)
    return text


# ---------------------------------------------------------------- entry point
def chat(message: str, portfolio_id: Optional[str] = None, timeframe: str = "1M",
         history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    ctx: Optional[Dict[str, Any]] = None
    if portfolio_id:
        try:
            ctx = build_analysis_context(portfolio_id, timeframe)
        except Exception:  # noqa: BLE001
            ctx = None

    news = gather_news(message, ctx)
    api_key = _load_env_key()
    answer: Optional[str] = None
    error: Optional[Dict[str, str]] = None

    if not api_key or api_key.startswith("mock_"):
        error = {"code": "NO_KEY", "message": "No Gemini API key found. Add GEMINI_API_KEY to .env, save, and restart the backend."}
    else:
        grounding = (f"PORTFOLIO_CONTEXT:\n{json.dumps(_compact_context(ctx), default=str) if ctx else 'No portfolio loaded.'}"
                     f"\n\nNEWS:\n{json.dumps(news, default=str)}")
        contents = _build_contents(history, f"{grounding}\n\nUSER QUESTION: {message}")
        answer, error = _call_gemini(api_key, contents)

    mode = "gemini" if answer else "rule_based"
    if not answer:
        answer = _rule_based_answer(message, ctx, news)

    return {
        "answer": _sanitize(answer),
        "mode": mode,
        "ai_status": {"ok": mode == "gemini", **(error or {})},
        "news": news,
        "portfolio_loaded": bool(ctx),
        "disclaimer": DISCLAIMER,
    }
