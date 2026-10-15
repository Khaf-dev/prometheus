"""
Tool Registry — Central registry untuk semua tools Prometheus.

Single source of truth untuk:
- Tool registration dan discovery
- LLM schema generation (function calling)
- Permission enforcement
- Execution dengan logging otomatis
- Timeout handling

Usage:
    registry = ToolRegistry()
    registry.register(MemoryReadTool())
    registry.register(FileSystemTool())

    # Get semua schemas untuk dikirim ke LLM
    schemas = registry.get_llm_schemas()

    # Execute tool dari LLM response
    result = await registry.execute("memory_read", query="fastapi auth")
"""

import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import BaseTool, PermissionLevel, ToolResult, ToolStatus

logger = logging.getLogger(__name__)

# Default timeout per permission level (seconds)
TIMEOUT_BY_PERMISSION = {
    PermissionLevel.READ_ONLY:   15,
    PermissionLevel.READ_WRITE:  30,
    PermissionLevel.DESTRUCTIVE: 30,
}


class ToolRegistry:
    """
    Central registry untuk semua tools.
    Di-inject ke agent via dependency injection.
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, tool: BaseTool) -> "ToolRegistry":
        """
        Register satu tool. Chainable:
            registry.register(ToolA()).register(ToolB())
        """
        if tool.name in self._tools:
            logger.warning(f"Tool '{tool.name}' sudah terdaftar — akan di-overwrite.")
        self._tools[tool.name] = tool
        logger.info(f"Tool registered: {tool.name} [{tool.permission.value}]")
        return self

    def register_many(self, tools: list[BaseTool]) -> "ToolRegistry":
        for tool in tools:
            self.register(tool)
        return self

    # ── Discovery ─────────────────────────────────────────────────────────────

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def get_llm_schemas(
        self,
        exclude_destructive: bool = False,
    ) -> list[dict]:
        """
        Return semua tool schemas dalam format LLM function calling.
        Anthropic dan OpenAI pakai format yang sama (input_schema).

        Args:
            exclude_destructive: kalau True, tools destructive tidak dikirim ke LLM.
                                 Berguna saat kita mau restrict agent.
        """
        schemas = []
        for tool in self._tools.values():
            if exclude_destructive and tool.permission == PermissionLevel.DESTRUCTIVE:
                continue
            schemas.append(tool.schema.to_llm_schema())
        return schemas

    # ── Execution ─────────────────────────────────────────────────────────────

    async def execute(
        self,
        tool_name: str,
        db: AsyncSession | None = None,
        conversation_id: str | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """
        Execute tool by name dengan:
        - Timeout enforcement
        - Error handling
        - Audit logging ke DB (kalau db session disediakan)

        Args:
            tool_name: nama tool yang mau dieksekusi
            db: async DB session untuk logging (optional)
            conversation_id: untuk audit trail (optional)
            **kwargs: parameter dari LLM tool call
        """
        import asyncio

        tool = self._tools.get(tool_name)
        if not tool:
            return ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error=f"Tool '{tool_name}' tidak ditemukan. Available: {self.list_names()}",
            )

        timeout = TIMEOUT_BY_PERMISSION[tool.permission]
        start = time.monotonic()

        # Selaraskan tipe parameter dengan schema sebelum tool dijalankan.
        #
        # LLM sering mengirim angka sebagai teks ("5" alih-alih 5) dan boolean
        # sebagai kata ("true"). Tanpa penyelarasan ini, setiap tool harus
        # mengurusnya sendiri — dan satu yang lupa akan gagal dengan TypeError
        # di tengah percakapan. Registry tahu tipe setiap parameter dari
        # schema, jadi di sinilah tempat yang tepat untuk menanganinya sekali.
        kwargs = _coerce_params(tool.schema, kwargs)

        try:
            logger.info(f"Executing tool: {tool_name} | params: {list(kwargs.keys())}")
            result = await asyncio.wait_for(
                tool.execute(**kwargs),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            result = ToolResult(
                status=ToolStatus.TIMEOUT,
                output=None,
                error=f"Tool '{tool_name}' timeout setelah {timeout}s.",
            )
        except Exception as e:
            logger.exception(f"Tool '{tool_name}' raised unexpected exception")
            result = ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error=str(e),
            )

        result.duration_ms = int((time.monotonic() - start) * 1000)

        # Audit log ke DB
        if db is not None:
            await self._log_execution(
                db=db,
                tool_name=tool_name,
                input_params=kwargs,
                result=result,
                conversation_id=conversation_id,
            )

        logger.info(
            f"Tool '{tool_name}' finished | "
            f"status={result.status.value} | "
            f"duration={result.duration_ms}ms"
        )
        return result

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _log_execution(
        self,
        db: AsyncSession,
        tool_name: str,
        input_params: dict,
        result: ToolResult,
        conversation_id: str | None,
    ) -> None:
        """Catat eksekusi tool ke tool_logs table — NFR-002 audit trail."""
        try:
            import json
            from sqlalchemy import text

            await db.execute(
                text("""
                    INSERT INTO tool_logs
                        (id, conversation_id, tool_name, input, output,
                         duration_ms, status, error_message, executed_at)
                    VALUES
                        (gen_random_uuid(), :conv_id, :tool_name, :input,
                         :output, :duration_ms, :status, :error, NOW())
                """),
                {
                    "conv_id": conversation_id,
                    "tool_name": tool_name,
                    "input": json.dumps(input_params, default=str),
                    "output": json.dumps(result.output, default=str) if result.output else None,
                    "duration_ms": result.duration_ms,
                    "status": result.status.value,
                    "error": result.error,
                },
            )
        except Exception:
            # Logging gagal tidak boleh crash execution
            logger.warning(f"Failed to log tool execution for '{tool_name}'", exc_info=True)

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        return f"<ToolRegistry tools={self.list_names()}>"


def _coerce_params(schema, params: dict) -> dict:
    """
    Ubah nilai parameter agar sesuai tipe yang dideklarasikan di schema.

    Nilai yang tidak bisa diubah dibiarkan apa adanya — tool boleh punya
    penanganan sendiri, dan memaksa di sini bisa menyembunyikan kesalahan
    yang sebenarnya perlu terlihat.
    """
    tipe = {p.name: p.type for p in schema.parameters}
    hasil = {}

    for nama, nilai in params.items():
        target = tipe.get(nama)

        if target == "integer" and not isinstance(nilai, bool):
            try:
                hasil[nama] = int(float(nilai))
                continue
            except (TypeError, ValueError):
                pass

        elif target == "number" and not isinstance(nilai, bool):
            try:
                hasil[nama] = float(nilai)
                continue
            except (TypeError, ValueError):
                pass

        elif target == "boolean" and isinstance(nilai, str):
            rendah = nilai.strip().lower()
            if rendah in ("true", "1", "yes", "ya"):
                hasil[nama] = True
                continue
            if rendah in ("false", "0", "no", "tidak"):
                hasil[nama] = False
                continue

        elif target == "string" and nilai is not None and not isinstance(nilai, str):
            hasil[nama] = str(nilai)
            continue

        hasil[nama] = nilai

    return hasil