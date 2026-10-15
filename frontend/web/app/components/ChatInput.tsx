"use client";

import { useRef, useState, KeyboardEvent, useEffect } from "react";
import { Send, Square, ChevronDown, Zap, Bot } from "lucide-react";
import type { Provider, TaskType } from "@/app/lib/api";

const PROVIDERS: { value: Provider | "auto"; label: string; color: string }[] = [
  { value: "auto",   label: "Auto",   color: "#7C6DFA" },
  { value: "claude", label: "Claude", color: "#CC785C" },
  { value: "gpt4o",  label: "GPT-4o", color: "#19C37D" },
  { value: "gemini", label: "Gemini", color: "#4A8EF6" },
  { value: "ollama", label: "Ollama", color: "#9D6EEB" },
];

const TASK_TYPES: { value: TaskType; label: string }[] = [
  { value: "general",  label: "General" },
  { value: "coding",   label: "Coding" },
  { value: "research", label: "Research" },
  { value: "quick",    label: "Quick" },
];

interface ChatInputProps {
  onSend: (content: string, opts?: { provider?: Provider; taskType?: TaskType }) => void;
  onStop: () => void;
  isLoading: boolean;
  sessionCost: number;
  disabled?: boolean;
  agentMode?: boolean;
}

export function ChatInput({ onSend, onStop, isLoading, sessionCost, disabled, agentMode }: ChatInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [value, setValue] = useState("");
  const [provider, setProvider] = useState<Provider | "auto">("auto");
  const [taskType, setTaskType] = useState<TaskType>("general");
  const [showProviderMenu, setShowProviderMenu] = useState(false);
  const [showTaskMenu, setShowTaskMenu] = useState(false);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      const target = e.target as HTMLElement;
      if (!target.closest("[data-dropdown]")) {
        setShowProviderMenu(false);
        setShowTaskMenu(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  function handleSend() {
    const trimmed = value.trim();
    if (!trimmed || isLoading || disabled) return;
    onSend(trimmed, {
      provider: provider === "auto" ? undefined : provider,
      taskType,
    });
    setValue("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  const activeProvider = PROVIDERS.find((p) => p.value === provider)!;
  const activeTask = TASK_TYPES.find((t) => t.value === taskType)!;

  return (
    <div className="p-4">
      {/* Agent mode indicator bar */}
      {agentMode && (
        <div className="mb-2 flex items-center gap-1.5 px-3 py-1.5 rounded-lg
                        bg-accent/5 border border-accent/20 animate-fade-in">
          <Zap size={11} className="text-accent" fill="currentColor" />
          <span className="text-[10px] text-accent font-medium">
            Agent mode — Prometheus akan reasoning dan menggunakan tools
          </span>
        </div>
      )}

      <div className={`rounded-2xl border transition-all duration-200
        ${disabled
          ? "border-[#2A2A35] opacity-50"
          : agentMode
            ? "border-accent/30 hover:border-accent/50 focus-within:border-accent/70"
            : "border-[#2A2A35] hover:border-[#7C6DFA]/30 focus-within:border-[#7C6DFA]/50"
        } bg-[#1C1C24]`}
      >
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled || isLoading}
          placeholder={
            disabled
              ? "Enter your API key to start..."
              : agentMode
                ? "Ask Prometheus anything — it will use tools and memory..."
                : "Message Prometheus…"
          }
          rows={1}
          className="w-full bg-transparent resize-none px-4 pt-3.5 pb-2
                     text-sm text-[#E4E4F0] placeholder:text-[#3D3D4A]
                     focus:outline-none leading-relaxed disabled:cursor-not-allowed"
        />

        {/* Toolbar */}
        <div className="flex items-center gap-2 px-3 pb-3">

          {/* Provider dropdown */}
          <div className="relative" data-dropdown>
            <button
              onClick={() => { setShowProviderMenu((v) => !v); setShowTaskMenu(false); }}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg
                         text-xs font-medium transition-all duration-100
                         border border-[#2A2A35] bg-[#111116] hover:border-[#7C6DFA]/30"
              style={{ color: activeProvider.color }}
            >
              <span className="w-1.5 h-1.5 rounded-full shrink-0"
                style={{ backgroundColor: activeProvider.color }} />
              {activeProvider.label}
              <ChevronDown size={10} className="text-[#6B6B7A]" />
            </button>

            {showProviderMenu && (
              <div className="absolute bottom-full mb-2 left-0 z-50 rounded-xl
                             shadow-2xl py-1 min-w-32.5 border border-[#2A2A35] bg-[#1C1C24]">
                {PROVIDERS.map((p) => (
                  <button
                    key={p.value}
                    onClick={() => { setProvider(p.value); setShowProviderMenu(false); }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-xs
                               transition-colors duration-100 hover:bg-[#111116]"
                    style={{ color: provider === p.value ? p.color : "#6B6B7A" }}
                  >
                    <span className="w-1.5 h-1.5 rounded-full shrink-0"
                      style={{ backgroundColor: p.color }} />
                    {p.label}
                    {provider === p.value && (
                      <span className="ml-auto text-[10px]" style={{ color: "#7C6DFA" }}>✓</span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Task type dropdown */}
          <div className="relative" data-dropdown>
            <button
              onClick={() => { setShowTaskMenu((v) => !v); setShowProviderMenu(false); }}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg
                         text-xs font-medium text-[#6B6B7A] transition-all duration-100
                         border border-[#2A2A35] bg-[#111116]
                         hover:border-[#7C6DFA]/30 hover:text-[#E4E4F0]"
            >
              {activeTask.label}
              <ChevronDown size={10} />
            </button>

            {showTaskMenu && (
              <div className="absolute bottom-full mb-2 left-0 z-50 rounded-xl
                             shadow-2xl py-1 min-w-27.5 border border-[#2A2A35] bg-[#1C1C24]">
                {TASK_TYPES.map((t) => (
                  <button
                    key={t.value}
                    onClick={() => { setTaskType(t.value); setShowTaskMenu(false); }}
                    className="w-full flex items-center justify-between gap-2
                               px-3 py-2 text-xs transition-colors duration-100
                               hover:bg-[#111116]"
                    style={{ color: taskType === t.value ? "#E4E4F0" : "#6B6B7A" }}
                  >
                    {t.label}
                    {taskType === t.value && (
                      <span className="text-[10px]" style={{ color: "#7C6DFA" }}>✓</span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Session cost */}
          {sessionCost > 0 && (
            <span className="text-[10px] text-[#3D3D4A] font-mono ml-1">
              ${sessionCost.toFixed(5)}
            </span>
          )}

          {/* Send / Stop */}
          <div className="ml-auto">
            {isLoading ? (
              <button
                onClick={onStop}
                className="w-8 h-8 rounded-lg flex items-center justify-center
                           border transition-all duration-100
                           bg-red-500/10 border-red-500/30 text-red-400
                           hover:bg-red-500/20"
                title="Stop"
              >
                <Square size={12} fill="currentColor" />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!value.trim() || disabled}
                className="w-8 h-8 rounded-lg flex items-center justify-center
                           text-white transition-all duration-100
                           disabled:opacity-30 disabled:cursor-not-allowed"
                style={{ backgroundColor: (!value.trim() || disabled) ? "#4F45A0" : "#7C6DFA" }}
                title="Send (Enter)"
              >
                <Send size={13} />
              </button>
            )}
          </div>
        </div>
      </div>

      <p className="text-center text-[10px] text-[#3D3D4A] mt-2">
        Enter to send · Shift+Enter for new line
        {agentMode && " · Agent mode active"}
      </p>
    </div>
  );
}