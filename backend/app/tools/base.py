"""
Tool Registry — Base classes dan interfaces.

Design philosophy:
- Setiap tool adalah self-contained unit: schema, permission, execution
- Tool registry adalah pluggable — tambah tool baru = buat file baru, register, selesai
- Permission system: read-only / read-write / destructive
- Destructive tools WAJIB minta konfirmasi sebelum execute
- Semua execution di-log ke tool_logs table (NFR-002: audit trail)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PermissionLevel(str, Enum):
    READ_ONLY   = "read_only"    # aman, tidak ada side effects
    READ_WRITE  = "read_write"   # modifikasi data internal Prometheus
    DESTRUCTIVE = "destructive"  # delete, external API write, shell exec


class ToolStatus(str, Enum):
    SUCCESS = "success"
    ERROR   = "error"
    TIMEOUT = "timeout"
    DENIED  = "denied"     # permission ditolak / konfirmasi tidak diberikan


@dataclass
class ToolParameter:
    """Schema satu parameter tool."""
    name: str
    type: str                           # "string" | "integer" | "boolean" | "array" | "object"
    description: str
    required: bool = True
    default: Any = None
    enum: list[str] | None = None       # kalau ada nilai yang diizinkan


@dataclass
class ToolSchema:
    """
    Schema lengkap satu tool — ini yang dikirim ke LLM
    sebagai function calling definition.
    """
    name: str
    description: str
    parameters: list[ToolParameter]
    permission: PermissionLevel
    requires_confirmation: bool = False  # override untuk destructive tools

    def to_llm_schema(self) -> dict:
        """Convert ke format OpenAI/Anthropic function calling schema."""
        properties = {}
        required = []

        for param in self.parameters:
            prop: dict[str, Any] = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum:
                prop["enum"] = param.enum
            if param.default is not None:
                prop["default"] = param.default

            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }


@dataclass
class ToolResult:
    """Hasil eksekusi tool."""
    status: ToolStatus
    output: Any                             # hasil yang dikembalikan ke agent
    error: str | None = None                # pesan error kalau status != SUCCESS
    duration_ms: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status == ToolStatus.SUCCESS

    def to_agent_message(self) -> str:
        """Format output untuk dikonsumsi agent sebagai observation."""
        if self.status == ToolStatus.ERROR:
            return f"[ERROR] {self.error}"
        if self.status == ToolStatus.TIMEOUT:
            return "[TIMEOUT] Tool execution timed out."
        if self.status == ToolStatus.DENIED:
            return "[DENIED] Action requires confirmation from user."
        if isinstance(self.output, str):
            return self.output
        import json
        # default=str: fallback untuk tipe yang tidak native JSON-serializable
        # (UUID, datetime, Decimal, dll). Tanpa ini, tool yang return UUID
        # (misal memory_write) bakal crash saat di-serialize ke agent message.
        return json.dumps(self.output, ensure_ascii=False, indent=2, default=str)


class BaseTool(ABC):
    """
    Abstract base class untuk semua tools di Prometheus.

    Cara tambah tool baru:
        1. Buat file: app/tools/implementations/my_tool.py
        2. Inherit BaseTool
        3. Implement schema() dan execute()
        4. Register di ToolRegistry

    Zero perubahan di core — sepenuhnya pluggable.
    """

    @property
    @abstractmethod
    def schema(self) -> ToolSchema:
        """Return schema tool ini — dipanggil saat register ke LLM."""
        ...

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """
        Eksekusi tool dengan parameter dari LLM.
        WAJIB handle exception internal — jangan biarkan exception bubble up.
        Return ToolResult dengan status ERROR kalau ada masalah.
        """
        ...

    @property
    def name(self) -> str:
        return self.schema.name

    @property
    def permission(self) -> PermissionLevel:
        return self.schema.permission

    @property
    def requires_confirmation(self) -> bool:
        return self.schema.requires_confirmation