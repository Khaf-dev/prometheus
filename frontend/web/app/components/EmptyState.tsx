"use client";

import { Zap, Code, BookOpen, Calendar } from "lucide-react";

const SUGGESTIONS = [
  { icon: Code,     label: "Review my code",    prompt: "Review this code for me:\n\n```python\n# paste code here\n```" },
  { icon: Zap,      label: "Debug an error",    prompt: "Help me debug this error:\n\n```\n# paste error here\n```" },
  { icon: BookOpen, label: "Search knowledge",  prompt: "What do I know about " },
  { icon: Calendar, label: "Plan my day",       prompt: "What's on my calendar today and what should I focus on?" },
];

interface EmptyStateProps {
  onSuggestion: (prompt: string) => void;
}

export function EmptyState({ onSuggestion }: EmptyStateProps) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-8 select-none">
      {/* Logo mark */}
      <div className="relative mb-6">
        <div className="w-14 h-14 rounded-2xl bg-accent/10 border border-accent/20
                        flex items-center justify-center">
          <Zap size={24} className="text-accent" />
        </div>
        {/* Glow */}
        <div className="absolute inset-0 rounded-2xl bg-accent/5 blur-xl" />
      </div>

      <h2 className="text-lg font-semibold text-text-primary mb-1.5 tracking-tight">
        What can I help you with?
      </h2>
      <p className="text-sm text-text-muted mb-8 text-center max-w-xs">
        Your personal AI — code reviews, debugging, research, scheduling. All in one place.
      </p>

      {/* Suggestion chips */}
      <div className="grid grid-cols-2 gap-2 w-full max-w-sm">
        {SUGGESTIONS.map(({ icon: Icon, label, prompt }) => (
          <button
            key={label}
            onClick={() => onSuggestion(prompt)}
            className="flex items-center gap-2.5 px-3.5 py-3 rounded-xl
                       bg-card hover:bg-border border border-border hover:border-accent/30
                       text-left transition-all duration-150 group"
          >
            <Icon size={14} className="text-text-dim group-hover:text-accent transition-colors" />
            <span className="text-xs text-text-muted group-hover:text-text-primary transition-colors">
              {label}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}