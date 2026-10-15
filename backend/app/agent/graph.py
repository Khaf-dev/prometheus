"""
Prometheus Agent Graph — LangGraph ReAct implementation.

Graph structure:
    START → memory → reason → [tool → reason]* → END

Semua edges dan nodes di-wire di sini.
Graph di-compile sekali dan reused (thread-safe di LangGraph).
"""

import logging
from functools import partial
from typing import AsyncIterator

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    MAX_ITERATIONS,
    memory_node,
    reason_node,
    should_continue,
    tool_node,
)
from app.agent.state import AgentState, AgentStep, StepType
from app.llm.gateway import LLMGateway
from app.memory.manager import MemoryManager
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def build_agent_graph(
    gateway: LLMGateway,
    tool_registry: ToolRegistry,
    memory_manager: MemoryManager,
):
    """
    Build dan compile LangGraph ReAct graph.

    Pattern: partial() untuk inject dependencies ke nodes
    tanpa mengubah signature yang diexpect LangGraph.

    Returns:
        Compiled LangGraph yang siap di-invoke
    """
    builder = StateGraph(AgentState)

    # ── Nodes ─────────────────────────────────────────────────────────────────
    builder.add_node(
        "memory",
        partial(memory_node, memory_manager=memory_manager),
    )
    builder.add_node(
        "reason",
        partial(reason_node, gateway=gateway, tool_registry=tool_registry),
    )
    builder.add_node(
        "tool",
        partial(tool_node, tool_registry=tool_registry),
    )

    # ── Edges ─────────────────────────────────────────────────────────────────
    builder.add_edge(START, "memory")       # selalu mulai dari memory retrieval
    builder.add_edge("memory", "reason")    # lalu reason

    # Conditional: setelah reason, apakah perlu tool atau selesai?
    builder.add_conditional_edges(
        "reason",
        should_continue,
        {
            "tool": "tool",   # perlu tool → ke tool node
            "end": END,       # sudah selesai → end
        },
    )

    # Setelah tool execute, balik ke reason untuk process hasil
    builder.add_edge("tool", "reason")

    return builder.compile()


class PrometheusAgent:
    """
    High-level interface untuk Prometheus ReAct agent.

    Usage:
        agent = PrometheusAgent(gateway, tool_registry, memory_manager)

        # Non-streaming
        result = await agent.run(messages=[...], conversation_id="...")

        # Streaming steps ke UI
        async for event in agent.stream(messages=[...]):
            print(event)
    """

    def __init__(
        self,
        gateway: LLMGateway,
        tool_registry: ToolRegistry,
        memory_manager: MemoryManager,
    ):
        self._graph = build_agent_graph(gateway, tool_registry, memory_manager)
        self._gateway = gateway
        self._tool_registry = tool_registry
        self._memory_manager = memory_manager

    async def run(
        self,
        messages: list[dict],
        conversation_id: str | None = None,
        provider: str | None = None,
        task_type: str = "general",
        pillar: str | None = None,
        focus_tool: str | None = None,
    ) -> dict:
        """
        Run agent sampai selesai, return final state.

        Returns:
            {
                "response": str,          # final response text
                "steps": list[dict],      # semua ReAct steps
                "error": str | None,
                "iterations": int,
            }
        """
        initial_state: AgentState = {
            "messages": messages,
            "memory_context": "",
            "system_prompt": "",
            "steps": [],
            "iteration": 0,
            "is_done": False,
            "final_response": "",
            "error": None,
            "provider": provider,
            "task_type": task_type,
            "conversation_id": conversation_id,
            "pillar": pillar,
            "focus_tool": focus_tool,
        }

        config = {"recursion_limit": MAX_ITERATIONS + 5}
        final_state = await self._graph.ainvoke(initial_state, config=config)

        return {
            "response": final_state.get("final_response", ""),
            "steps": [s.to_dict() if isinstance(s, AgentStep) else s
                      for s in final_state.get("steps", [])],
            "error": final_state.get("error"),
            "iterations": final_state.get("iteration", 0),
        }

    async def stream_steps(
        self,
        messages: list[dict],
        conversation_id: str | None = None,
        provider: str | None = None,
        task_type: str = "general",
        pillar: str | None = None,
        focus_tool: str | None = None,
    ) -> AsyncIterator[dict]:
        """
        Stream agent execution step by step.

        Yield events:
            {"type": "step", "step": {...}}         # setiap ReAct step
            {"type": "response", "content": "..."}  # final response
            {"type": "done", "iterations": N}       # selesai
            {"type": "error", "message": "..."}     # error
        """
        initial_state: AgentState = {
            "messages": messages,
            "memory_context": "",
            "system_prompt": "",
            "steps": [],
            "iteration": 0,
            "is_done": False,
            "final_response": "",
            "error": None,
            "provider": provider,
            "task_type": task_type,
            "conversation_id": conversation_id,
            "pillar": pillar,
            "focus_tool": focus_tool,
        }

        config = {"recursion_limit": MAX_ITERATIONS + 5}
        seen_steps = 0

        try:
            async for chunk in self._graph.astream(initial_state, config=config):
                # chunk adalah dict {node_name: state_update}
                for node_name, state_update in chunk.items():
                    # Stream new steps
                    new_steps = state_update.get("steps", [])
                    if isinstance(new_steps, list):
                        for step in new_steps[seen_steps:]:
                            s = step if isinstance(step, dict) else step.to_dict()
                            yield {"type": "step", "step": s, "node": node_name}
                        seen_steps = len(new_steps)

                    # Stream final response
                    if state_update.get("is_done") and state_update.get("final_response"):
                        yield {
                            "type": "response",
                            "content": state_update["final_response"],
                        }

                    # Stream error
                    if state_update.get("error"):
                        yield {
                            "type": "error",
                            "message": state_update["error"],
                        }

            yield {"type": "done", "iterations": initial_state.get("iteration", 0)}

        except Exception as e:
            logger.exception("Agent stream failed")
            yield {"type": "error", "message": str(e)}