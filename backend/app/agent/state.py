"""
Agent State — LangGraph state definition untuk Prometheus ReAct agent.

State adalah "memori kerja" agent selama satu conversation turn.
Setiap node di graph bisa baca dan update state ini.

Design:
- messages disimpan sebagai PLAIN DICT (bukan LangChain Message objects).
  Kita TIDAK pakai langgraph.graph.message.add_messages reducer karena
  reducer itu auto-convert dict ke HumanMessage/AIMessage — yang justru
  bikin downstream code (memory_node, reason_node) harus tau dua format
  berbeda. Custom reducer di bawah ini lebih predictable: selalu dict in, dict out.
- Steps accumulate (tidak di-overwrite) untuk UI transparency
- Error captured di state, bukan di-raise ke atas
"""

from enum import Enum
from typing import Annotated, Any

from typing_extensions import TypedDict


class StepType(str, Enum):
    THINKING    = "thinking"
    TOOL_CALL   = "tool_call"
    TOOL_RESULT = "tool_result"
    RESPONSE    = "response"
    ERROR       = "error"


class AgentStep:
    """Satu step dalam ReAct loop — ditampilkan di UI sebagai thinking panel."""
    __slots__ = ("type", "content", "tool_name", "tool_input", "metadata")

    def __init__(
        self,
        type: StepType,
        content: str,
        tool_name: str | None = None,
        tool_input: dict | None = None,
        metadata: dict | None = None,
    ):
        self.type = type
        self.content = content
        self.tool_name = tool_name
        self.tool_input = tool_input or {}
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "metadata": self.metadata,
        }


def _append_steps(left: list, right: list | AgentStep) -> list:
    """Reducer: steps selalu di-append, tidak di-overwrite."""
    if isinstance(right, AgentStep):
        return left + [right]
    return left + right


def _append_messages(left: list[dict], right: list[dict]) -> list[dict]:
    """
    Reducer untuk messages — PLAIN DICT, bukan LangChain Message objects.

    Ini sengaja custom (bukan pakai langgraph.graph.message.add_messages)
    supaya format selalu konsisten: list[dict] dengan key "role" dan "content".
    Semua node (memory_node, reason_node, tool_node) bisa pakai .get() dengan aman.
    """
    return left + right


class AgentState(TypedDict):
    """State lengkap satu agent run."""

    # ── Conversation ──────────────────────────────────────────────────────────
    # Plain dict messages: [{"role": "user", "content": "..."}, ...]
    messages: Annotated[list[dict], _append_messages]

    # ── Context ───────────────────────────────────────────────────────────────
    memory_context: str
    system_prompt: str

    # ── Execution ─────────────────────────────────────────────────────────────
    steps: Annotated[list[AgentStep], _append_steps]
    iteration: int
    is_done: bool
    final_response: str
    error: str | None

    # ── Config ────────────────────────────────────────────────────────────────
    provider: str | None
    task_type: str
    conversation_id: str | None

    # Wilayah yang sedang dibuka pengguna — dipakai menyaring tool
    pillar: str | None

    # Kemampuan yang diklik pengguna di peta bintang. Ini niat yang sudah
    # dinyatakan secara eksplisit — jangan dibuang, pakai untuk mengarahkan
    # model ke tool yang benar.
    focus_tool: str | None