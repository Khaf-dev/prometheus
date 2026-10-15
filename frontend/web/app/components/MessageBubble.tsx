"use client";

import { User, Zap, AlertCircle, Copy, Check, ChevronDown, ChevronRight, Wrench, Brain, CheckCircle } from "lucide-react";
import { useState } from "react";
import type { ChatMessage, AgentStep } from "@/app/hooks/useChat";

const PROVIDER_COLORS: Record<string, string> = {
  claude: "#CC785C",
  gpt4o:  "#19C37D",
  gemini: "#4A8EF6",
  ollama: "#9D6EEB",
};

const PROVIDER_LABELS: Record<string, string> = {
  claude: "Claude",
  gpt4o:  "GPT-4o",
  gemini: "Gemini",
  ollama: "Ollama",
};

const STEP_ICONS: Record<string, React.ReactNode> = {
  thinking:    <Brain size={11} />,
  tool_call:   <Wrench size={11} />,
  tool_result: <CheckCircle size={11} />,
  error:       <AlertCircle size={11} />,
};

const STEP_COLORS: Record<string, string> = {
  thinking:    "#7C6DFA",
  tool_call:   "#CC785C",
  tool_result: "#19C37D",
  error:       "#EF4444",
};

function AgentSteps({ steps }: { steps: AgentStep[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!steps || steps.length === 0) return null;

  return (
    <div className="mb-2">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5 text-[10px] text-text-dim
                   hover:text-text-muted transition-colors duration-100 group"
      >
        {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        <span>{steps.length} agent step{steps.length !== 1 ? "s" : ""}</span>
      </button>

      {expanded && (
        <div className="mt-1.5 space-y-1 pl-3 border-l border-border animate-fade-in">
          {steps.map((step, i) => (
            <div key={i} className="flex items-start gap-1.5 text-[10px]">
              <span style={{ color: STEP_COLORS[step.type] ?? "#6B6B7A" }} className="mt-0.5 shrink-0">
                {STEP_ICONS[step.type]}
              </span>
              <div className="min-w-0">
                <span className="text-text-dim">{step.content}</span>
                {step.tool_name && (
                  <span className="ml-1.5 font-mono text-[9px] px-1 py-0.5
                                   rounded bg-card border border-border text-text-dim">
                    {step.tool_name}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface Props {
  message: ChatMessage;
}

export function MessageBubble({ message }: Props) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";
  const providerColor = PROVIDER_COLORS[message.provider ?? ""] ?? "#7C6DFA";

  async function handleCopy() {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className={`flex gap-3 group animate-slide-up ${isUser ? "flex-row-reverse" : ""}`}>
      {/* Avatar */}
      <div
        className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5
          ${isUser ? "bg-accent/20 border border-accent/30" : "bg-card border border-border"}`}
        style={!isUser ? { borderColor: `${providerColor}30` } : {}}
      >
        {isUser ? (
          <User size={13} className="text-accent" />
        ) : (
          <Zap size={13} style={{ color: providerColor }} />
        )}
      </div>

      {/* Content */}
      <div className={`flex-1 max-w-[85%] ${isUser ? "items-end" : "items-start"} flex flex-col gap-1`}>

        {/* Provider badge + agent indicator */}
        {!isUser && (message.provider || message.isAgentMode) && (
          <div className="flex items-center gap-1.5">
            {message.provider && (
              <span
                className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-card border border-border"
                style={{ color: providerColor }}
              >
                {PROVIDER_LABELS[message.provider] ?? message.provider}
              </span>
            )}
            {message.isAgentMode && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 border border-accent/20 text-accent">
                agent
              </span>
            )}
          </div>
        )}

        {/* Agent steps (collapsible) */}
        {!isUser && message.steps && message.steps.length > 0 && (
          <AgentSteps steps={message.steps} />
        )}

        {/* Bubble */}
        <div className={`relative px-4 py-3 rounded-2xl text-sm leading-relaxed
          ${isUser
            ? "bg-accent/15 border border-accent/20 text-text-primary rounded-tr-sm"
            : "bg-card border border-border text-text-primary rounded-tl-sm"
          }
          ${message.error ? "border-red-500/30 bg-red-500/5" : ""}
        `}>
          {message.error ? (
            <div className="flex items-start gap-2 text-red-400">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <span className="text-xs">{message.error}</span>
            </div>
          ) : (
            <>
              <div
                className={`message-content ${message.isStreaming ? "cursor-blink" : ""}`}
                dangerouslySetInnerHTML={{ __html: formatContent(message.content) }}
              />
              {/* Streaming dots saat content masih kosong */}
              {message.isStreaming && message.content === "" && (
                <div className="flex gap-1 items-center h-4">
                  {[0, 1, 2].map((i) => (
                    <div
                      key={i}
                      className="w-1 h-1 rounded-full bg-accent animate-glow-pulse"
                      style={{ animationDelay: `${i * 0.2}s` }}
                    />
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        {/* Metadata row */}
        {!isUser && !message.isStreaming && message.content && (
          <div className="flex items-center gap-3 px-1">
            {message.usage && (
              <span className="text-[10px] text-text-dim font-mono">
                {message.usage.tokens_output} tok
                {message.usage.cost_usd > 0 && ` · $${message.usage.cost_usd.toFixed(5)}`}
              </span>
            )}
            <button
              onClick={handleCopy}
              className="opacity-0 group-hover:opacity-100 transition-opacity
                         text-text-dim hover:text-text-muted p-0.5 rounded"
              title="Copy"
            >
              {copied ? <Check size={11} className="text-green-400" /> : <Copy size={11} />}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function formatContent(text: string): string {
  if (!text) return "";
  let html = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) =>
    `<pre><code class="language-${lang}">${code.trim()}</code></pre>`
  );
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html
    .split(/\n\n+/)
    .map((p) => {
      if (p.startsWith("<pre>")) return p;
      return `<p>${p.replace(/\n/g, "<br>")}</p>`;
    })
    .join("");
  return html;
}