# 🔥 Prometheus

> **Kaff's Agentic Framework for Intelligence**
> Personal AI assistant — code reviews, debugging, research, scheduling. All in one place.

**Stack:** FastAPI · Next.js 15 · PostgreSQL 17 + pgvector · LangGraph · React Native (Expo)
**Status:** Phase 1 complete — LLM Gateway · Auth · Database · Chat UI · Agent ReAct Loop · Memory System · Tool Registry

---

## Prerequisites

| Tool           | Version | Notes                           |
| -------------- | ------- | ------------------------------- |
| Python         | 3.11+   | Tested: 3.13                    |
| Node.js        | 18+     | Recommended: `nvm`              |
| Docker Desktop | 24+     | Untuk PostgreSQL + Redis        |
| Ollama         | latest  | Untuk local LLM (free, offline) |

---

## Quick Start

```bash
# 1. Setup backend
cd backend
cp .env.example .env          # isi API keys
make setup                    # Docker up + DB migrate
make dev                      # FastAPI di http://localhost:8000

# 2. Bootstrap API key (terminal baru)
# Windows PowerShell:
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/auth/setup" `
  -Method POST -ContentType "application/json" `
  -Body '{"key_name": "laptop"}'

# Linux/macOS:
curl -s -X POST http://localhost:8000/api/v1/auth/setup \
  -H "Content-Type: application/json" \
  -d '{"key_name": "laptop"}'

# 3. Setup frontend (terminal baru)
cd ../frontend/web
npm install
npm run dev                   # Next.js di http://localhost:3000
```

Buka `http://localhost:3000` → masukkan API key → selesai. 🚀

---

## Project Structure

```
prometheus/
├── backend/
│   ├── app/
│   │   ├── agent/
│   │   │   ├── state.py          # AgentState TypedDict + reducers
│   │   │   ├── nodes.py          # memory_node, reason_node, tool_node
│   │   │   └── graph.py          # PrometheusAgent + LangGraph assembly
│   │   ├── api/v1/
│   │   │   ├── auth.py           # API key CRUD endpoints
│   │   │   └── chat.py           # SSE streaming (direct + agent mode)
│   │   ├── auth/
│   │   │   ├── crypto.py         # Key generation + bcrypt hashing
│   │   │   ├── middleware.py     # FastAPI AuthDep
│   │   │   └── models.py         # ApiKey model (re-export dari core)
│   │   ├── core/
│   │   │   ├── models.py         # Semua 8 SQLAlchemy models
│   │   │   ├── database.py       # Async session management
│   │   │   ├── dependencies.py   # FastAPI DI (gateway, agent, memory, tools)
│   │   │   └── vector_search.py  # pgvector cosine + hybrid RRF search
│   │   ├── llm/
│   │   │   ├── base.py           # Abstract BaseLLMProvider
│   │   │   ├── gateway.py        # Multi-provider orchestrator + fallback
│   │   │   └── providers/
│   │   │       ├── claude.py     # Anthropic SDK
│   │   │       ├── gpt4o.py      # OpenAI SDK
│   │   │       ├── gemini.py     # Google GenAI SDK (google-genai, bukan deprecated google-generativeai)
│   │   │       └── ollama.py     # Ollama HTTP client
│   │   ├── memory/
│   │   │   ├── embeddings.py     # EmbeddingService (OpenAI + Ollama fallback)
│   │   │   └── manager.py        # MemoryManager (retrieve, save, dedupe, recency)
│   │   ├── tools/
│   │   │   ├── base.py           # Abstract BaseTool + ToolSchema + ToolResult
│   │   │   ├── registry.py       # ToolRegistry (register, execute, audit log)
│   │   │   └── implementations/
│   │   │       ├── memory_read.py
│   │   │       ├── memory_write.py
│   │   │       └── filesystem.py  # FileRead + FileWrite + FileList (sandboxed)
│   │   └── main.py               # FastAPI entry point + .env loader
│   ├── alembic/
│   │   ├── env.py                # Async-compatible migration env
│   │   └── versions/
│   │       └── 001_initial.py    # Full schema + pgvector + HNSW indexes
│   ├── scripts/
│   │   ├── init_db.sql           # pgvector extensions + tuning
│   │   └── wait_for_db.py        # Cross-platform DB health check (Windows-safe)
│   ├── docker-compose.yml
│   ├── Makefile
│   ├── requirements.txt
│   └── .env.example
│
└── frontend/web/
    └── app/
        ├── components/
        │   ├── Sidebar.tsx        # Conversations list
        │   ├── MessageBubble.tsx  # Chat bubbles + provider badge + streaming glow
        │   ├── ChatInput.tsx      # Input + provider selector + task type (custom dropdown)
        │   ├── EmptyState.tsx     # Welcome + suggestion chips
        │   └── SetupModal.tsx     # First-time API key setup
        ├── hooks/useChat.ts       # SSE streaming state management
        ├── lib/api.ts             # FastAPI client + streamChat()
        ├── page.tsx               # Main layout
        └── globals.css            # Design tokens + animations
```

---

## Environment Variables

Copy `.env.example` ke `.env` di `backend/`:

```bash
# ── LLM Providers ─────────────────────────────────────────────────────────────
# Kosongin yang tidak dipakai — provider tidak akan di-register
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...

# ── Ollama (lokal, no key needed) ─────────────────────────────────────────────
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_DEFAULT_MODEL=llama3.1:8b      # sesuaikan dengan VRAM GPU lo
OLLAMA_EMBED_MODEL=mxbai-embed-large  # 1024 dim, di-pad ke 1536

# ── Database ──────────────────────────────────────────────────────────────────
# PENTING: pakai 127.0.0.1, BUKAN localhost — fix untuk asyncpg di Windows
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@127.0.0.1:5432/prometheus

# ── App ───────────────────────────────────────────────────────────────────────
SECRET_KEY=ganti-ini-dengan-random-string-panjang
DEBUG=true
```

> **Minimal setup tanpa cloud:** Cukup jalankan Ollama lokal — zero cost, zero API key.

---

## Agent Mode

Prometheus punya dua mode chat via endpoint yang sama:

```bash
# Mode 1: Direct LLM (cepat, tidak pakai tools)
POST /api/v1/agent/chat
{
  "messages": [...],
  "agent_mode": false    # default
}

# Mode 2: ReAct Agent (reasoning + tool use + memory)
POST /api/v1/agent/chat
{
  "messages": [...],
  "agent_mode": true,
  "provider": "ollama"   # optional, kalau tidak diisi = auto-route
}
```

### ReAct Flow

```
User message
    ↓
[memory_node]    → pgvector similarity search (recency weighted)
    ↓
[reason_node]    → LLM decide: tool atau jawab langsung?
    ↓ (kalau butuh tool)
[tool_node]      → execute tool, dapat result
    ↓
[reason_node]    → loop, process hasil tool
    ↓ (selesai)
SSE stream       → response + steps ke frontend
```

### SSE Events

```json
{"type": "step",     "step": {"type": "thinking", ...}}   // agent thinking
{"type": "step",     "step": {"type": "tool_call", ...}}   // agent mau pakai tool
{"type": "step",     "step": {"type": "tool_result", ...}} // hasil tool
{"type": "delta",    "delta": "teks...", "provider": "ollama"} // streaming response
{"type": "done",     "iterations": 2}                       // selesai
{"type": "error",    "message": "..."}                      // kalau ada error
```

### Tools yang tersedia

| Tool           | Permission  | Deskripsi                           |
| -------------- | ----------- | ----------------------------------- |
| `memory_read`  | read_only   | Semantic search ke long-term memory |
| `memory_write` | read_write  | Simpan memory baru + auto-embed     |
| `file_read`    | read_only   | Baca file (sandboxed)               |
| `file_list`    | read_only   | List isi direktori                  |
| `file_write`   | destructive | Tulis file (butuh konfirmasi)       |

---

## LLM Gateway

### Auto-routing

| Task Type  | Primary | Fallback 1 | Fallback 2      |
| ---------- | ------- | ---------- | --------------- |
| `coding`   | Claude  | GPT-4o     | Ollama          |
| `research` | Gemini  | Claude     | Ollama          |
| `quick`    | Ollama  | Claude     | —               |
| `general`  | Claude  | GPT-4o     | Gemini → Ollama |

### Test endpoint

```powershell
# Windows PowerShell
$API_KEY = "prom_xxxxx"
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/agent/providers" `
  -Headers @{Authorization = "Bearer $API_KEY"}
```

```bash
# Linux/macOS
curl -s http://localhost:8000/api/v1/agent/providers \
  -H "Authorization: Bearer prom_xxxxx"
```

---

## Memory System

### Embedding providers

| Provider | Model                  | Dimensi            | Notes                         |
| -------- | ---------------------- | ------------------ | ----------------------------- |
| OpenAI   | text-embedding-3-small | 1536 (native)      | Best quality, $0.02/1M tokens |
| Ollama   | mxbai-embed-large      | 1024 → padded 1536 | Free, offline                 |

> **Catatan:** `mxbai-embed-large` output asli 1024 dim, di-zero-pad ke 1536 supaya
> compatible dengan schema pgvector. Accuracy antar-session Ollama tetap konsisten.

### Recency weighting

Memory retrieval pakai combined score:

```
final_score = 0.75 × similarity + 0.25 × recency
recency = 1 / (1 + days_since_created)
```

Memory hari ini boost +0.25, memory 30 hari lalu hanya +0.03.

---

## Ollama Setup

### Windows — Model storage ke drive lain

Installer Ollama tidak punya opsi pilih lokasi — dia auto-install di `%LOCALAPPDATA%`.
Untuk redirect **model storage** (yang berat ~5GB+) ke drive lain:

```powershell
# 1. Install Ollama normal (file installer kecil, aman di C)
.\OllamaSetup.exe

# 2. Set permanent env variable SEBELUM pull model
New-Item -ItemType Directory -Path "D:\ollama-models" -Force
[System.Environment]::SetEnvironmentVariable("OLLAMA_MODELS", "D:\ollama-models", "User")

# 3. Buka terminal BARU (env var butuh sesi baru), verifikasi
echo $env:OLLAMA_MODELS   # harus: D:\ollama-models

# 4. Kill Ollama process lama (dia auto-start dan baca env var lama)
Stop-Process -Name ollama -Force -ErrorAction SilentlyContinue

# 5. Pull model (akan masuk ke D:\ollama-models)
ollama pull llama3.1:8b
ollama pull mxbai-embed-large
```

> **Gotcha:** Kalau Ollama service auto-start via startup shortcut sebelum lo set
> env var, model tetap akan ke C. Kill process dulu, baru pull.

### Model recommendations berdasarkan GPU VRAM

| GPU VRAM | Model       | Notes                                  |
| -------- | ----------- | -------------------------------------- |
| < 4GB    | llama3.2:3b | Minimal, masih cukup untuk quick tasks |
| 4-6GB    | llama3.1:8b | **Recommended** untuk RTX 3050 6GB     |
| 8-12GB   | qwen2.5:14b | Balance quality vs size                |
| 16GB+    | qwen2.5:32b | High quality, lambat di CPU            |

> **RTX 3050 6GB:** `qwen2.5:14b` butuh ~9-10GB VRAM → crash CUDA.
> Gunakan `llama3.1:8b` (~5GB VRAM). Kalau CUDA crash, Ollama bisa fallback ke CPU
> tapi jauh lebih lambat.

### Verify model tersimpan di drive yang benar

```powershell
ollama list
Get-ChildItem D:\ollama-models -Recurse -File | `
  Measure-Object -Property Length -Sum | `
  ForEach-Object { "{0:N2} GB" -f ($_.Sum / 1GB) }
```

---

## Windows Development — Known Issues & Fixes

Semua ini ditemukan langsung saat development di Windows 11 + Docker Desktop.

### 1. Docker context harus `desktop-linux`

```powershell
docker context ls
docker context use desktop-linux
```

Kalau salah context, `docker compose up` gagal dengan error `npipe:////./pipe/dockerDesktopLinuxEngine`.

### 2. PostgreSQL Windows service conflict di port 5432

Kalau punya PostgreSQL terinstall lokal di Windows, dia rebutan port 5432 dengan Docker container.

```powershell
# Cek conflict
netstat -ano | findstr :5432
tasklist | findstr "<PID>"

# Stop dan disable autostart PostgreSQL lokal
net stop postgresql-x64-17
Set-Service -Name "postgresql-x64-17" -StartupType Disabled
```

### 3. asyncpg: pakai `127.0.0.1`, bukan `localhost`

`localhost` di Windows kadang resolve ke IPv6 (`::1`) yang tidak di-listen asyncpg.

```bash
# ❌ Salah
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@localhost:5432/prometheus

# ✅ Benar
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@127.0.0.1:5432/prometheus
```

### 4. Makefile bash syntax tidak jalan di Windows

`until ... do; sleep 1; done` adalah bash syntax. Makefile kita pakai Python script
sebagai pengganti yang cross-platform:

```powershell
# Gunakan ini, bukan `make setup` langsung
docker compose up -d
python scripts/wait_for_db.py
alembic upgrade head
```

### 5. `.env` tidak auto-load oleh FastAPI

FastAPI tidak otomatis baca `.env` — harus explicit `load_dotenv()` di `main.py`
**sebelum** semua import yang pakai `os.getenv()`.

```python
# app/main.py — WAJIB di paling atas sebelum import lain
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")
```

### 6. Alembic tidak baca `.env`

Sama seperti FastAPI — Alembic juga tidak auto-load `.env`.
File `alembic/env.py` kita sudah handle ini dengan manual parse `.env`.

Atau set manual sebelum jalankan alembic:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://USER:PASSWORD@127.0.0.1:5432/prometheus"
alembic upgrade head
```

---

## Troubleshooting

**Server crash: `Can't instantiate abstract class XProvider`**

File provider (`claude.py`, `gemini.py`, `ollama.py`) ke-truncate saat copy-paste.
Cek ukuran file:

```powershell
Get-ChildItem D:\prometheus\backend\app\llm\providers -Filter "*.py" | Select-Object Name, Length
```

Claude: ~5KB, Gemini: ~5KB, Ollama: ~4KB, GPT4o: ~4KB.
Kalau jauh lebih kecil, re-copy dari output yang benar.

**`ImportError: cannot import name 'AgentDep'`**

File `app/core/dependencies.py` masih versi lama (Phase 0).
Replace dengan versi Phase 1 yang include `get_prometheus_agent()` dan `AgentDep`.

**`ModuleNotFoundError: No module named 'backend'`**

Ada typo import `from backend.app.tools.base import ...`.
Harusnya `from app.tools.base import ...`.

**`HumanMessage object has no attribute 'get'`**

LangGraph `add_messages` reducer auto-convert dict ke LangChain objects.
Fix: gunakan custom reducer `_append_messages` di `agent/state.py` (sudah fixed).

**Ollama tool calling: `500 Internal Server Error`**

Kemungkinan VRAM tidak cukup untuk load model + tools. Gejala: `CUDA error: shared object initialization failed`.
Solusi: ganti ke model yang lebih kecil (`llama3.1:8b` untuk 6GB VRAM).

**`Connection refused` saat akses backend dari frontend**

```powershell
curl http://localhost:8000/health
# Harus return: {"status": "ok", "service": "prometheus"}
```

**`Setup sudah pernah dilakukan`**

Database sudah punya key. Reset kalau key hilang:

```powershell
docker compose exec postgres psql -U nama user -d prometheus -c "TRUNCATE api_keys RESTART IDENTITY CASCADE;"
```

---

## Development Phases

```
✅ Phase 0 — Foundation
   LLM Gateway · Auth (API key + bcrypt) · PostgreSQL + pgvector · Next.js Chat UI

✅ Phase 1 — Core Agent
   LangGraph ReAct Loop · Tool Registry (5 tools) · Memory System (embed + pgvector)
   Ollama native tool calling · SSE step transparency

⬜ Phase 2 — Integrations
   Google Calendar · GitHub · Browser automation (Playwright)

⬜ Phase 3 — Knowledge Base
   Document ingestion · RAG hybrid search · Notion integration

⬜ Phase 4 — Polish + Mobile
   Daily briefing · React Native (Expo) · Cost dashboard · Observability
```

---

## API Reference

Dokumentasi interaktif tersedia di:

- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

Semua endpoint kecuali `/health` dan `/api/v1/auth/setup` butuh:

```
Authorization: Bearer prom_xxxxx
```

---

_Prometheus v0.2 — Phase 0 + Phase 1 complete · Juni 2026_
