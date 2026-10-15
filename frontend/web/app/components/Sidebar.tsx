"use client";

import { useState } from "react";
import {
  Plus, MessageSquare, Flame, Settings, Trash2, ChevronRight
} from "lucide-react";
import type { Conversation } from "@/app/lib/api";

interface SidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete?: (id: string) => void;
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export function Sidebar({ conversations, activeId, onSelect, onNew, onDelete }: SidebarProps) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  async function handleDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    setDeletingId(id);
    try {
      await onDelete?.(id);
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <aside className="w-65 shrink-0 h-screen flex flex-col bg-surface border-r border-border">
      {/* Header */}
      <div className="p-4 border-b border-border">
        <div className="flex items-center gap-2.5 mb-4">
          <div className="w-7 h-7 rounded-lg bg-accent flex items-center justify-center shrink-0">
            <Flame size={14} className="text-white" />
          </div>
          <span className="font-semibold text-text-primary tracking-tight text-sm">
            Prometheus
          </span>
          <span className="ml-auto text-[10px] text-text-dim font-mono bg-card px-1.5 py-0.5 rounded border border-border">
            v0.2
          </span>
        </div>

        <button
          onClick={onNew}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg
                     bg-accent/10 hover:bg-accent/20 border border-accent/20
                     hover:border-accent/40 transition-all duration-150
                     text-accent text-sm font-medium group"
        >
          <Plus size={14} className="group-hover:rotate-90 transition-transform duration-200" />
          New conversation
        </button>
      </div>

      {/* Conversations list */}
      <div className="flex-1 overflow-y-auto py-2">
        {conversations.length === 0 ? (
          <div className="px-4 py-8 text-center">
            <MessageSquare size={24} className="mx-auto mb-2 text-text-dim" />
            <p className="text-text-muted text-xs">No conversations yet.</p>
            <p className="text-text-dim text-xs mt-1">Start one above.</p>
          </div>
        ) : (
          <ul className="space-y-0.5 px-2">
            {conversations.map((conv) => {
              const isActive = conv.id === activeId;
              const isDeleting = deletingId === conv.id;
              return (
                <li key={conv.id}>
                  <div
                    onClick={() => onSelect(conv.id)}
                    onMouseEnter={() => setHoveredId(conv.id)}
                    onMouseLeave={() => setHoveredId(null)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelect(conv.id);
                      }
                    }}
                    className={`w-full text-left px-3 py-2.5 rounded-lg transition-all duration-100 flex items-start gap-2.5 group relative cursor-pointer
                      ${isActive ? "bg-accent/10 text-text-primary" : "hover:bg-card text-text-muted hover:text-text-primary"}
                      ${isDeleting ? "opacity-40" : ""}`}
                  >
                    {isActive && (
                      <div className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-accent rounded-r-full" />
                    )}
                    <MessageSquare
                      size={13}
                      className={`mt-0.5 shrink-0 ${isActive ? "text-accent" : "text-text-dim"}`}
                    />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium truncate leading-tight">
                        {conv.title ?? "Untitled"}
                      </p>
                      <p className="text-[10px] text-text-dim mt-0.5">
                        {timeAgo(conv.updated_at)}
                        {conv.message_count > 0 && (
                          <span className="ml-1.5 text-text-dim">· {conv.message_count} msgs</span>
                        )}
                      </p>
                    </div>

                    {/* Delete button — show on hover */}
                    {hoveredId === conv.id && onDelete && (
                      <button
                        onClick={(e) => handleDelete(e, conv.id)}
                        className="shrink-0 p-0.5 rounded text-text-dim
                                   hover:text-red-400 hover:bg-red-400/10
                                   transition-all duration-100 animate-fade-in"
                        title="Delete conversation"
                      >
                        <Trash2 size={11} />
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Footer */}
      <div className="p-3 border-t border-border">
        <button className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg
                           text-text-muted hover:text-text-primary hover:bg-card
                           transition-all duration-100 text-xs">
          <Settings size={13} />
          Settings
        </button>
      </div>
    </aside>
  );
}