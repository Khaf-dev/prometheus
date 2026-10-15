"use client";

import { useCallback, useRef, useState } from "react";
import { streamChat, type Provider, type TaskType } from "@/app/lib/api";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  provider?: string;
  isStreaming?: boolean;
  isAgentMode?: boolean;
  steps?: AgentStep[];
  usage?: { tokens_input: number; tokens_output: number; cost_usd: number; model: string };
  error?: string;
}

export interface AgentStep {
  type: "thinking" | "tool_call" | "tool_result" | "response" | "error";
  content: string;
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

function genId() {
  return Math.random().toString(36).slice(2, 10);
}

interface SendOptions {
  provider?: Provider;
  taskType?: TaskType;
  agentMode?: boolean;
  onMessagesSaved?: (
    userContent: string,
    assistantContent: string,
    usage?: { tokens_input: number; tokens_output: number; cost_usd: number; model: string }
  ) => Promise<void>;
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [sessionCost, setSessionCost] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(
    async (content: string, opts?: SendOptions) => {
      if (!content.trim() || isLoading) return;

      const userMsg: ChatMessage = { id: genId(), role: "user", content };
      const assistantId = genId();
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
        isStreaming: true,
        isAgentMode: opts?.agentMode ?? false,
        steps: [],
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setIsLoading(true);

      const history = [...messages, userMsg].map((m) => ({
        role: m.role as "user" | "assistant",
        content: m.content,
      }));

      abortRef.current = new AbortController();
      let finalContent = "";
      let finalUsage: ChatMessage["usage"] | undefined;

      await streamChat({
        messages: history,
        provider: opts?.provider,
        taskType: opts?.taskType,
        agentMode: opts?.agentMode ?? false,
        signal: abortRef.current.signal,

        onDelta: (delta, provider) => {
          finalContent += delta;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, content: m.content + delta, provider }
                : m
            )
          );
        },

        onStep: (step: AgentStep) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, steps: [...(m.steps ?? []), step] }
                : m
            )
          );
        },

        onDone: (usage, cost) => {
          finalUsage = usage;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, isStreaming: false, usage }
                : m
            )
          );
          if (cost) setSessionCost((prev) => prev + cost);
          setIsLoading(false);

          // Callback untuk save ke DB
          opts?.onMessagesSaved?.(content, finalContent, finalUsage);
        },

        onError: (message) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, isStreaming: false, content: "", error: message }
                : m
            )
          );
          setIsLoading(false);
        },
      });
    },
    [messages, isLoading]
  );

  const stopStreaming = useCallback(() => {
    if (!abortRef.current) return; // Guard simpel

    abortRef.current?.abort();
    setMessages((prev) =>
      prev.map((m) => (m.isStreaming ? { ...m, isStreaming: false } : m))
    );
    setIsLoading(false);
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setSessionCost(0);
  }, []);

  // Expose setMessages untuk load conversation dari DB
  return { messages, isLoading, sessionCost, sendMessage, stopStreaming, clearMessages, setMessages };
}