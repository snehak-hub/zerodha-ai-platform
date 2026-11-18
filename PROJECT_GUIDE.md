# Project Guide – Zerodha AI Financial Intelligence Platform

## 1. What is this project?

A web platform that connects to a Zerodha portfolio (live login, Kite CSV upload, or built-in sample portfolios),
calculates risk and performance statistics with deterministic Python code, and uses Gemini AI to explain the results
in plain language. A built-in **AI chatbot** lets the user talk to their portfolio: ask about holdings, risk, sectors,
latest news, or any general finance question.

## 2. Agenda / Goals

| # | Goal | How it is achieved |
|---|------|--------------------|
| 1 | Make portfolio risk easy to understand | HHI, beta, volatility, drawdown, sector weights shown on a dashboard with AI explanations |
| 2 | Zero-hallucination numbers | All maths is done in Python (`pandas`/`NumPy`); the LLM only explains, never calculates |
| 3 | Stay regulation-friendly | SEBI-style validator blocks buy/sell instructions, target prices and guaranteed-return claims |
| 4 | Be auditable | Every MCP tool call is logged with latency; every analysis has an audit snapshot |
| 5 | Be conversational | Chatbot answers from portfolio statistics + latest news + general market knowledge |
| 6 | Work offline / without API key | Deterministic fallbacks for both the analysis and the chatbot |
| 7 | Multi-user & deploy-ready | Accounts, per-user data in PostgreSQL, migrations, Docker + Nginx, production safety checks |

## 3. Technology used

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js (App Router), React, TypeScript, Tailwind CSS |
| Backend | Python 3.11+, FastAPI, Uvicorn, Pydantic |
| Database | **PostgreSQL 16** via SQLAlchemy 2 + Alembic migrations (JSONB for insights/audits) |
| Auth | Email + password (bcrypt), JWT bearer tokens, login rate-limiting |
| Analytics | pandas, NumPy (HHI, beta, volatility, max drawdown, sector exposure) |
| AI | Google Gemini 2.5 Flash via `google-genai` |
| Tool layer | Model Context Protocol (MCP)-style governed tool server with audit logs |
| Market data | Yahoo Finance quotes (live sync), sample CSV data (offline) |
| News | Google News RSS (live, no key needed) with sample CSV fallback |
| Broker | Zerodha Kite (web login with 2FA/enctoken, Kite Connect, Console CSV) |
| Testing | pytest, httpx |
| Deployment | Docker, docker-compose, Nginx, AWS EC2 / Render / Vercel |

## 4. How the AI chatbot works

```
User question ──► POST /api/chat
                    │
                    ├─ 1. Portfolio context  (build_analysis_context → MCP tools → Python analytics)
                    ├─ 2. Latest news        (MCP tool fetch_live_news → Google News RSS; falls back to sample CSV)
                    ├─ 3. Conversation history (last 10 turns, loaded from PostgreSQL)
                    ▼
              Gemini 2.5 Flash  (system prompt: use only given numbers, no buy/sell advice)
                    ▼
              Compliance filter (blocks prohibited phrases) ──► answer + sources shown in the chat widget
```

* Portfolio questions use the exact computed numbers, so the answer matches the dashboard.
* News questions use the latest headlines, shown as "Sources" under the reply.
* General questions ("what is beta?") are answered from the model's knowledge and linked back to the user's portfolio.
* If `GEMINI_API_KEY` is missing or the API fails, a rule-based answerer replies from the same data, so the chat never breaks.

Files added for the chatbot:

| File | Purpose |
|------|---------|
| `ai_workflows/chatbot/chat_service.py` | Builds context, fetches news, calls Gemini, applies compliance filter, offline fallback |
| `mcp_server/tools/live_news_tools.py` | `fetch_live_news` MCP tool (Google News RSS) |
| `backend/main.py` | New `POST /api/chat` endpoint |
| `frontend/components/ChatBot.tsx` | Floating chat widget on the dashboard |
| `tests/chatbot_tests/test_chatbot.py` | Automated tests |

## 5. Running the project in VS Code

### Prerequisites
* [Python 3.11+](https://www.python.org/downloads/) (tick **Add to PATH** on Windows)
* [Node.js 18+](https://nodejs.org/)
* [Git](https://git-scm.com/) and [VS Code](https://code.visualstudio.com/)
* Recommended VS Code extensions: **Python**, **Pylance**, **ESLint**, **Tailwind CSS IntelliSense**
* A free Gemini API key from <https://aistudio.google.com/apikey> (optional but recommended)

### Step 1 – Open the project
```bash
git clone https://github.com/crushervp/zerodha-ai-financial-intelligence-platform.git
cd zerodha-ai-financial-intelligence-platform
code .
```

### Step 2 – Configure the environment
Copy `.env.example` to `.env` (project root) and set at least your Gemini key (optional – the app works without it):
```
GEMINI_API_KEY=your_key_here
```
The other defaults in `.env.example` already match the local PostgreSQL in Step 3. For a server, see **Section 8**.

### Step 3 – Start PostgreSQL

Pick **one** option:

**A) Docker (recommended).** Install [Docker Desktop](https://www.docker.com/products/docker-desktop/), then in a VS Code terminal:
```bash
docker compose up -d db
```
This creates the `zerodha_ai` database with the user/password from `.env` (data is kept in a Docker volume).

**B) PostgreSQL installed on your PC.** Install from <https://www.postgresql.org/download/>, then in `psql`:
```sql
CREATE USER zerodha WITH PASSWORD 'zerodha_pw';
CREATE DATABASE zerodha_ai OWNER zerodha;
```

**C) Free cloud database** (Neon, Supabase, Render): create a project and paste its connection string into
`DATABASE_URL` in `.env` (add `?sslmode=require` if it is not already there).

### Step 4 – Start the backend (VS Code Terminal 1: *Terminal → New Terminal*)

**Windows (PowerShell)**
```powershell
python -m venv env
env\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head          # creates all database tables (run again after every git pull)
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```
If activation is blocked, run `Set-ExecutionPolicy -Scope Process Bypass` first.

**macOS / Linux**
```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```
Check <http://localhost:8000/health> (should show `"database": "OK"`) and the API docs at <http://localhost:8000/docs>.

> In VS Code press `Ctrl+Shift+P` → **Python: Select Interpreter** → choose the `env` interpreter.

### Step 5 – Start the frontend (Terminal 2: click the **+** icon)
```bash
cd frontend
npm install
npm run dev
```
Open <http://localhost:3000>.

### Step 6 – Try it
0. Open the site and **create an account** (every user's data is separate).
1. Pick a sample portfolio (or connect Zerodha / upload a Kite CSV) and click **Analyse**.
2. Click the orange **💬 Ask AI** button at the bottom-right.
3. Try:
   * "Summarize my portfolio"
   * "Which stock is hurting my returns the most?"
   * "Is my portfolio too concentrated? Explain HHI."
   * "Any latest news on HDFCBANK?"
   * "What is the difference between beta and volatility?"

### Step 7 – Run the tests
```bash
PYTHONPATH=. pytest tests/ -v          # Windows PowerShell: $env:PYTHONPATH="."; pytest tests/ -v
```
Tests use a temporary SQLite file by default. To run the same suite on PostgreSQL:
```bash
TEST_DATABASE_URL=postgresql://zerodha:zerodha_pw@localhost:5432/zerodha_ai_test PYTHONPATH=. pytest tests/
```
(create the `zerodha_ai_test` database first, and run `alembic upgrade head` against it with `DATABASE_URL` set to the same URL).

### Troubleshooting
| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError: backend` | Set `PYTHONPATH=.` and run from the project root |
| Chat says "could not reach the server" | Backend must be running on port 8000 |
| Chat answers look basic/rule-based | `GEMINI_API_KEY` missing or invalid in `.env`; restart the backend |
| No live news in chat | No internet or the RSS feed is blocked; sample news is used automatically |
| `npm install` errors | Use Node 18+; delete `node_modules` and `package-lock.json` and retry |
| `connection refused` / `could not connect to server` | PostgreSQL is not running: `docker compose up -d db`, and check `DATABASE_URL` in `.env` |
| `relation "users" does not exist` | Run `alembic upgrade head` |
| Logged out after every backend restart | Set a fixed `JWT_SECRET` in `.env` |
| Port already in use | Stop the other process or change the port in the command |

## 6. Folder structure
```
backend/       FastAPI app, auth, analytics engine, Zerodha service, db/ (SQLAlchemy models)
migrations/    Alembic database migrations
mcp_server/    Governed tool dispatcher + tools (portfolio, market, news, live news, analytics)
ai_workflows/  Context builder, prompts, Gemini analysis, validation, chatbot
frontend/      Next.js dashboard, operations page, ChatBot widget
data/          Sample portfolios, market data, news
tests/         Analytics, AI-output, functional and chatbot tests
```

## 7. Database (PostgreSQL)

| Table | Purpose |
|-------|---------|
| `users` | Accounts: email, bcrypt password hash, name |
| `broker_connections` | One Zerodha link per user (client id, name, **masked** token hint) |
| `holdings` | Each user's holdings (extra broker fields kept in a JSONB column) |
| `analysis_jobs` | Every analysis run: owner, portfolio, timeframe, status |
| `insights` | Completed analysis result (JSONB) |
| `audit_logs` | Compliance snapshot per job: context, output, tool calls (JSONB) |
| `feedback` | Helpful / not-helpful feedback per user |
| `chat_messages` | Chatbot history per user |

**What is and is not stored:** holdings, analysis results, chat history and feedback are stored per user.
Zerodha **passwords, TOTP codes and full session tokens are never stored**. Every query is filtered by the logged-in
user, and one user can never read another user's jobs, audits, holdings or chats (covered by automated tests).
`DELETE /api/auth/me` deletes the account and all of its data.

**Changing the schema:** edit `backend/db/models.py`, then
```bash
alembic revision --autogenerate -m "describe change"   # review the generated file in migrations/versions/
alembic upgrade head
```
**Backups:**
```bash
docker compose exec db pg_dump -U zerodha zerodha_ai > backup_$(date +%F).sql          # backup
docker compose exec -T db psql -U zerodha zerodha_ai < backup_2026-01-01.sql           # restore
```
On managed Postgres (RDS, Neon, Supabase, Render) enable automated daily backups in the provider's dashboard.

## 8. Deploying

**Docker on a server (AWS EC2 etc.)** – full steps in [DEPLOY_AWS.md](DEPLOY_AWS.md). In short:
```bash
cp .env.example .env     # then edit it, see checklist below
docker compose up -d --build
```
The backend container runs `alembic upgrade head` automatically before starting. Postgres is **not** exposed to the internet.

**Render:** `render.yaml` creates the web service and a PostgreSQL database and wires `DATABASE_URL` and a generated `JWT_SECRET`.
Set `CORS_ORIGINS` to your frontend URL.

**Production checklist**
- [ ] `ENVIRONMENT=production`
- [ ] `JWT_SECRET` – random, 32+ characters (`python -c "import secrets; print(secrets.token_urlsafe(48))"`)
- [ ] `POSTGRES_PASSWORD` – long and random (compose builds `DATABASE_URL` from it)
- [ ] `CORS_ORIGINS` – your real site URL(s), not `*` (the backend refuses to start otherwise)
- [ ] `GEMINI_API_KEY` set; **never commit `.env`** (it is in `.gitignore`)
- [ ] HTTPS enabled (Let's Encrypt, see DEPLOY_AWS.md) – logins and tokens must not travel over plain HTTP
- [ ] Automated database backups enabled
- [ ] If you store real Indian users' financial data: add a privacy policy and consent screen (DPDP Act 2023); get a legal review before public launch

## 9. Disclaimer
This project is for education and risk awareness. It is not SEBI-registered investment advice and does not
recommend buying or selling any security.
