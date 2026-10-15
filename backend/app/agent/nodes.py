"""
Agent Nodes — Setiap fungsi di sini adalah satu node dalam LangGraph graph.

ReAct Flow:
    START
      ↓
    [memory_node]     → inject relevant memories ke context
      ↓
    [reason_node]     → LLM decide: jawab langsung atau pakai tool?
      ↓ (kalau tool)
    [tool_node]       → execute tool, dapat result
      ↓
    [reason_node]     → loop balik, LLM process tool result
      ↓ (kalau selesai)
    [respond_node]    → format final response
      ↓
    END

Setiap node return dict yang di-merge ke AgentState.
"""

import json
import logging
from typing import Any

from app.agent.state import AgentState, AgentStep, StepType
from app.llm.base import LLMConfig, Message
from app.llm.gateway import LLMGateway, ProviderName, TaskType
from app.memory.manager import MemoryManager
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10  # hard limit ReAct loop depth

# ── Penyaringan tool per wilayah ──────────────────────────────────────────────
#
# Mengirim SEMUA tool ke model kecil (llama3.1:8b) punya dua masalah nyata:
#
# 1. Prompt membengkak. 18 schema JSON bisa ratusan token sebelum percakapan
#    dimulai. Di mesin 6GB, buffer komputasi llama.cpp gagal dialokasi dan
#    server mati dengan "failed to allocate compute buffers".
# 2. Model jadi bingung memilih. Makin banyak pilihan, makin sering salah tool.
#
# Karena UI sudah tahu wilayah mana yang dibuka pengguna, kita kirim hanya
# tool wilayah itu. Prompt menyusut ~3x dan pilihannya jadi tajam.
#
# memory_read ikut di semua wilayah karena konteks lama selalu relevan.
PILLAR_TOOLS: dict[str, set[str]] = {
    "personal": {
        "calendar_list_events", "calendar_create_event",
        "calendar_check_free_busy", "calendar_delete_event",
        "memory_read",
    },
    "dev": {
        "github_list_prs", "github_get_pr", "github_list_issues",
        "github_get_repo", "github_search_code",
        "file_read", "memory_read",
    },
    "brain": {
        "rag_search", "knowledge_list", "memory_read", "memory_write",
        "browser_fetch", "browser_search", "file_list",
    },
}


def _tools_for_pillar(tool_registry, pillar: str | None) -> list[dict]:
    """
    Ambil schema tool yang relevan untuk satu wilayah.
    Kalau wilayah tidak dikenal (atau tidak dikirim), pakai semua tool —
    perilaku lama tetap jalan.
    """
    schemas = tool_registry.get_llm_schemas(exclude_destructive=False)
    allowed = PILLAR_TOOLS.get(pillar or "")
    if not allowed:
        return schemas
    picked = [s for s in schemas if s["name"] in allowed]
    return picked or schemas

PROMETHEUS_SYSTEM = """Kamu Prometheus, asisten pribadi Kaff.

Daftar tool beserta kegunaannya dikirim terpisah — jangan menebak nama tool
yang tidak ada di daftar itu.

Cara kerja:
- Panggil tool bila perlu data nyata; jangan mengarang isinya.
- Setelah menerima hasil tool, jawab dengan bahasa biasa.
- Ikuti bahasa yang dipakai Kaff.
- Kata ganti: saat Kaff menulis "gua"/"gue"/"aku", yang dia maksud DIRINYA
  SENDIRI. Saat dia menulis "lo"/"lu"/"kamu", yang dia maksud KAMU.
  Jadi "siapakah gua?" berarti "siapakah Kaff?" — jawab tentang dia.
  Sebut dirimu "aku"; jangan pernah memakai "gua" untuk dirimu sendiri.
- Memori berisi fakta tentang KAFF, bukan tentangmu. "Nama user adalah Kaff"
  berarti lawan bicaramu bernama Kaff.
- Kalau ragu, tanya. Jangan mengarang fakta.
- PAKAI isi hasil tool untuk menjawab. Kalau hasilnya berisi data, jawab
  dari data itu meski tidak sempurna cocok dengan pertanyaan.
- Yang dilarang: mengarang isi yang tidak ada di hasil tool. Kalau hasilnya
  benar-benar kosong, katakan apa adanya."""


async def memory_node(
    state: AgentState,
    memory_manager: MemoryManager,
) -> dict:
    """
    Node pertama: retrieve relevant memories sebelum agent mulai reasoning.
    Memory di-inject ke state sebagai context string.
    """
    # Ambil pesan terakhir user sebagai query
    last_user_msg = ""
    for msg in reversed(state["messages"]):
        if msg.get("role") == "user":
            last_user_msg = msg.get("content", "")
            break

    if not last_user_msg:
        return {"memory_context": ""}

    try:
        memories = await memory_manager.retrieve_relevant(
            query=last_user_msg,
            limit=5,
            # Untuk cloud LLM, exclude local_only memories
            include_local_only=False,
        )
        context = memory_manager.format_for_context(memories, include_local_only=False)
        if context:
            logger.info(f"Memory node: injecting {len(memories)} memories")
        return {"memory_context": context}

    except Exception as e:
        logger.warning(f"Memory node failed: {e}")
        return {"memory_context": ""}


async def reason_node(
    state: AgentState,
    gateway: LLMGateway,
    tool_registry: ToolRegistry,
) -> dict:
    """
    Core ReAct node: LLM decide apakah perlu tool atau langsung jawab.

    Output:
    - Kalau butuh tool: tambah AgentStep TOOL_CALL, set messages dengan tool_use block
    - Kalau sudah bisa jawab: set is_done=True, isi final_response
    """
    if state["iteration"] >= MAX_ITERATIONS:
        logger.warning("Max iterations reached")
        return {
            "is_done": True,
            "final_response": (
                "Maaf, gua sudah coba beberapa kali tapi tidak bisa menyelesaikan "
                "request ini dalam batas langkah yang diizinkan."
            ),
            "error": "max_iterations_reached",
        }

    # Build system prompt dengan memory context
    system = state.get("system_prompt") or PROMETHEUS_SYSTEM
    memory_ctx = state.get("memory_context", "")
    if memory_ctx:
        system = f"{system}\n\n{memory_ctx}"

    # Pengguna sudah memilih kemampuan tertentu di peta bintang. Itu niat yang
    # eksplisit — sayang kalau dibuang. Model kecil sering salah pilih antara
    # tool yang mirip (mis. rag_search vs browser_fetch); petunjuk ini
    # mengarahkannya tanpa memaksa, jadi dia masih boleh memilih tool lain
    # kalau pertanyaannya ternyata butuh yang berbeda.
    focus_tool = state.get("focus_tool")
    if focus_tool:
        system = (
            f"{system}\n\n"
            f"Pengguna membuka kemampuan '{focus_tool}'. "
            f"Dahulukan tool itu kecuali pertanyaannya jelas membutuhkan yang lain."
        )

    # Cek apakah sudah ada tool result di conversation ini
    has_tool_result = any(m.get("role") == "tool" for m in state["messages"])

    # FORCE ANSWER MODE — kunci untuk model kecil (llama3.1:8b).
    #
    # Masalah: model kecil "tergoda" terus manggil tool selama tool schema
    # masih ada di context. Gejalanya dia nulis JSON tool call sebagai TEKS
    # di jawaban, atau loop manggil tool berulang tanpa pernah menjawab.
    #
    # Solusi: begitu sudah ada tool result, kirim request TANPA tools sama
    # sekali. Model jadi tidak punya pilihan selain menyusun jawaban natural
    # dari hasil tool yang sudah ada. Ini bikin ReAct loop selesai dengan
    # bersih di iterasi kedua.
    if has_tool_result:
        tool_schemas = []
        system = (
            f"{system}\n\n"
            "PENTING: Kamu sudah menerima hasil dari tool. Sekarang JAWAB "
            "pertanyaan user dengan bahasa natural berdasarkan hasil tersebut. "
            "JANGAN memanggil tool lagi. JANGAN output JSON. Langsung jawab."
        )
    else:
        tool_schemas = _tools_for_pillar(tool_registry, state.get("pillar"))

    # Convert messages ke format internal.
    #
    # PENTING: tool result HARUS ikut dikirim ke LLM. Kalau di-filter out,
    # LLM tidak pernah lihat hasil tool yang baru saja dieksekusi — akibatnya
    # dia mengarang jawaban ("tidak menemukan informasi") walaupun tool-nya
    # sukses. Tool message di-convert jadi "user" role dengan prefix jelas,
    # karena tidak semua provider support role "tool" secara native.
    messages = []
    for m in state["messages"]:
        role = m.get("role")
        content = m.get("content", "")

        if role in ("user", "assistant"):
            # Skip assistant message yang kosong (cuma wrapper tool_calls)
            if role == "assistant" and not content.strip():
                continue
            messages.append(Message(role=role, content=content))
        elif role == "tool":
            tool_name = m.get("tool_name", "tool")
            messages.append(Message(
                role="user",
                content=f"[Hasil dari {tool_name}]\n{content}",
            ))

    # Build LLM config
    provider_name = None
    if p := state.get("provider"):
        try:
            provider_name = ProviderName(p)
        except ValueError:
            pass

    task_type = TaskType(state.get("task_type", "general"))
    config = LLMConfig(
        provider=provider_name,
        task_type=task_type,
        system_prompt=system,
        max_tokens=4096,
    )

    # Add thinking step
    thinking_step = AgentStep(
        type=StepType.THINKING,
        content=f"Iterasi {state['iteration'] + 1}: Analyzing request...",
    )

    try:
        # Call LLM — non-streaming untuk reasoning node
        # (streaming di respond_node untuk final response)
        response = await _call_llm_with_tools(
            gateway=gateway,
            messages=messages,
            config=config,
            tools=tool_schemas,
        )

        # Parse response
        if response.get("tool_calls"):
            # LLM mau pakai tool
            tool_call = response["tool_calls"][0]
            tool_step = AgentStep(
                type=StepType.TOOL_CALL,
                content=f"Calling tool: {tool_call['name']}",
                tool_name=tool_call["name"],
                tool_input=tool_call["input"],
            )
            return {
                "steps": [thinking_step, tool_step],
                "iteration": state["iteration"] + 1,
                "messages": [{
                    "role": "assistant",
                    "content": response.get("content", ""),
                    "tool_calls": response["tool_calls"],
                }],
            }
        else:
            # LLM punya final answer
            final_text = _clean_response(response.get("content", ""))
            return {
                "steps": [thinking_step],
                "is_done": True,
                "final_response": final_text,
                "iteration": state["iteration"] + 1,
                "messages": [{"role": "assistant", "content": final_text}],
            }

    except Exception as e:
        logger.exception("reason_node failed")
        return {
            "steps": [thinking_step],
            "is_done": True,
            "error": str(e),
            "final_response": f"Maaf, terjadi error: {e}",
        }


async def tool_node(
    state: AgentState,
    tool_registry: ToolRegistry,
) -> dict:
    """
    Execute tool yang diminta oleh reason_node.
    Result dimasukkan kembali ke messages sebagai tool_result.
    """
    # Ambil tool call dari message terakhir
    last_msg = state["messages"][-1] if state["messages"] else {}
    tool_calls = last_msg.get("tool_calls", [])

    if not tool_calls:
        return {"error": "tool_node called tapi tidak ada tool_calls"}

    tool_call = tool_calls[0]
    tool_name = tool_call["name"]
    tool_input = tool_call.get("input", {})

    # Execute
    result = await tool_registry.execute(
        tool_name=tool_name,
        conversation_id=state.get("conversation_id"),
        **tool_input,
    )

    result_content = result.to_agent_message()

    result_step = AgentStep(
        type=StepType.TOOL_RESULT,
        content=f"Tool {tool_name}: {result_content[:200]}{'...' if len(result_content) > 200 else ''}",
        tool_name=tool_name,
        metadata={"status": result.status.value, "duration_ms": result.duration_ms},
    )

    return {
        "steps": [result_step],
        "messages": [{
            "role": "tool",
            "content": result_content,
            "tool_name": tool_name,
            "tool_call_id": tool_call.get("id", tool_name),
        }],
    }


# ── Router ────────────────────────────────────────────────────────────────────

def should_continue(state: AgentState) -> str:
    """
    Router function untuk LangGraph conditional edge.
    Return nama node berikutnya berdasarkan state saat ini.
    """
    if state.get("is_done") or state.get("error"):
        return "end"

    # Cek apakah ada tool call di pesan terakhir
    last_msg = state["messages"][-1] if state["messages"] else {}
    if last_msg.get("tool_calls"):
        return "tool"

    return "end"


# ── Response cleanup ──────────────────────────────────────────────────────────

def _clean_response(text: str) -> str:
    """
    Bersihkan jawaban dari JSON tool call yang "bocor" sebagai teks.

    Model kecil kadang nulis tool call sebagai teks biasa di jawaban, misal:
        'Jawabannya: {"name": "memory_read", "parameters": {...}}'
    Ini sampah yang tidak boleh dilihat user. Kita buang pola JSON yang
    mengandung key "name" + "parameters"/"arguments".
    """
    import re

    if not text:
        return text

    # Buang blok JSON yang terlihat seperti tool call
    cleaned = re.sub(
        r'\{[^{}]*"name"\s*:\s*"[^"]+?"[^{}]*"(?:parameters|arguments)"\s*:\s*\{[^{}]*\}[^{}]*\}',
        "",
        text,
        flags=re.DOTALL,
    )

    # Buang sisa kalimat pengantar yang jadi menggantung
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    # Kalau setelah dibersihkan jadi kosong/terlalu pendek, kasih fallback
    if len(cleaned) < 10:
        return (
            "Maaf, gua kesulitan menyusun jawaban dari hasil pencarian. "
            "Coba tanya ulang dengan kalimat yang lebih spesifik ya."
        )

    return cleaned


# ── LLM helper ───────────────────────────────────────────────────────────────

async def _call_llm_with_tools(
    gateway: LLMGateway,
    messages: list[Message],
    config: LLMConfig,
    tools: list[dict],
) -> dict:
    """
    Call LLM dengan tool definitions — routing berdasarkan config.provider.

    Provider yang support native tool calling di implementasi ini:
    - claude (Anthropic native tool_use)
    - ollama (OpenAI-compatible /api/chat dengan "tools" param, model harus
      support tool calling — qwen2.5 series sudah support)

    Provider lain (gpt4o, gemini) ATAU saat provider tidak diset eksplisit:
    fallback ke Claude kalau API key ada, baru ke direct completion tanpa tools.

    Returns:
        {"content": str, "tool_calls": list | None}
    """
    requested = config.provider.value if config.provider else None

    if requested == "ollama":
        return await _call_ollama_with_tools(gateway, messages, config, tools)

    # Default / explicit claude / unset provider → coba Claude dulu
    return await _call_claude_with_tools(gateway, messages, config, tools)


async def _call_claude_with_tools(
    gateway: LLMGateway,
    messages: list[Message],
    config: LLMConfig,
    tools: list[dict],
) -> dict:
    import os
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")

    if not api_key:
        logger.warning(
            "ANTHROPIC_API_KEY tidak ditemukan. Fallback ke direct LLM tanpa tools."
        )
        response = await gateway.complete(messages, config)
        return {"content": response.content, "tool_calls": None}

    client = anthropic.AsyncAnthropic(api_key=api_key)

    anthropic_tools = [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
        }
        for t in tools
    ]

    anthropic_messages = []
    for msg in messages:
        # Tool result sudah di-convert jadi role "user" di reason_node,
        # tapi jaga-jaga kalau ada yang lolos — convert, jangan dibuang.
        role = "user" if msg.role == "tool" else msg.role
        anthropic_messages.append({
            "role": role,
            "content": msg.content,
        })

    response = await client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        system=config.system_prompt or PROMETHEUS_SYSTEM,
        messages=anthropic_messages,
        tools=anthropic_tools,
    )

    tool_calls = []
    text_content = ""
    for block in response.content:
        if block.type == "text":
            text_content += block.text
        elif block.type == "tool_use":
            tool_calls.append({
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })

    return {
        "content": text_content,
        "tool_calls": tool_calls if tool_calls else None,
    }


async def _call_ollama_with_tools(
    gateway: LLMGateway,
    messages: list[Message],
    config: LLMConfig,
    tools: list[dict],
) -> dict:
    """
    Ollama tool calling via OpenAI-compatible /api/chat endpoint.
    Model harus support tool calling (qwen2.5, llama3.1, mistral-nemo, dll).
    """
    import os
    import httpx

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_DEFAULT_MODEL", "qwen2.5:14b")

    # Convert tools ke format Ollama (OpenAI function calling style)
    ollama_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]

    ollama_messages = [
        {"role": "system", "content": config.system_prompt or PROMETHEUS_SYSTEM}
    ]
    for msg in messages:
        # Tool result JANGAN dibuang — LLM butuh lihat hasil tool untuk
        # menyusun jawaban. Convert role "tool" jadi "user" karena Ollama
        # /api/chat tidak selalu handle role "tool" dengan konsisten.
        role = "user" if msg.role == "tool" else msg.role
        ollama_messages.append({"role": role, "content": msg.content})

    # Model lokal bisa lambat — apalagi lewat Vulkan atau CPU. Timeout 120 detik
    # terlalu ketat: prompt 300+ token saja bisa makan 30 detik hanya untuk
    # dibaca. Beri kelonggaran, dan biarkan diatur lewat .env supaya mesin yang
    # lebih kencang bisa memperketatnya kembali.
    timeout = float(os.getenv("OLLAMA_TIMEOUT", "300"))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "messages": ollama_messages,
                    "tools": ollama_tools,
                    "stream": False,
                    # Model bernalar (qwen3.5, deepseek-r1) menghabiskan ratusan
                    # token untuk "berpikir" sebelum menjawab. Untuk agent yang
                    # tugasnya memilih tool lalu merangkum hasil, itu beban
                    # tanpa manfaat sepadan: lambat dan memakan konteks.
                    # Model tanpa fitur ini mengabaikan field-nya.
                    "think": False,
                    "options": {
                        # Suhu rendah agar pemanggilan tool konsisten. Bawaan
                        # qwen3.5 adalah 1.0 — terlalu longgar untuk JSON.
                        "temperature": 0.3,
                    },
                },
            )
            response.raise_for_status()
            data = response.json()

        message = data.get("message", {})
        text_content = message.get("content", "")
        raw_tool_calls = message.get("tool_calls", [])

        tool_calls = [
            {
                "id": tc.get("function", {}).get("name", "unknown"),
                "name": tc.get("function", {}).get("name"),
                "input": tc.get("function", {}).get("arguments", {}),
            }
            for tc in raw_tool_calls
        ]

        return {
            "content": text_content,
            "tool_calls": tool_calls if tool_calls else None,
        }

    except Exception as e:
        logger.warning(f"Ollama tool calling failed: {e}. Fallback ke direct completion.")
        response = await gateway.complete(messages, config)
        return {"content": response.content, "tool_calls": None}