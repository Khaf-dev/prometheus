"use client";

import { useState } from "react";
import { Flame, Key, Eye, EyeOff, AlertCircle, CheckCircle } from "lucide-react";
import { setupApiKey, setApiKey } from "@/app/lib/api";

interface SetupModalProps {
  onComplete: () => void;
}

export function SetupModal({ onComplete }: SetupModalProps) {
  const [mode, setMode] = useState<"enter" | "generate">("enter");
  const [keyInput, setKeyInput] = useState("");
  const [keyName, setKeyName] = useState("laptop");
  const [showKey, setShowKey] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [generatedKey, setGeneratedKey] = useState("");

  async function handleGenerate() {
    setLoading(true);
    setError("");
    try {
      const { api_key } = await setupApiKey(keyName);
      setGeneratedKey(api_key);
      setApiKey(api_key);
    } catch (e: any) {
      setError(e.message ?? "Failed to generate key");
    } finally {
      setLoading(false);
    }
  }

  function handleEnterKey() {
    if (!keyInput.startsWith("prom_")) {
      setError("Key harus diawali dengan 'prom_'");
      return;
    }
    setApiKey(keyInput);
    onComplete();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-base/90 backdrop-blur-sm">
      <div className="w-full max-w-md mx-4 bg-card border border-border rounded-2xl
                      shadow-2xl shadow-black/50 animate-slide-up">
        {/* Header */}
        <div className="p-6 border-b border-border">
          <div className="flex items-center gap-3 mb-1">
            <div className="w-9 h-9 rounded-xl bg-accent flex items-center justify-center">
              <Flame size={18} className="text-white" />
            </div>
            <div>
              <h1 className="text-base font-semibold text-text-primary">Welcome to Prometheus</h1>
              <p className="text-xs text-text-muted">Connect to your backend to get started</p>
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-border">
          {(["enter", "generate"] as const).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setError(""); }}
              className={`flex-1 py-3 text-xs font-medium transition-colors duration-100
                ${mode === m
                  ? "text-accent border-b-2 border-accent -mb-px"
                  : "text-text-muted hover:text-text-primary"}`}
            >
              {m === "enter" ? "Enter existing key" : "Generate new key"}
            </button>
          ))}
        </div>

        <div className="p-6 space-y-4">
          {mode === "enter" ? (
            <>
              <div>
                <label className="block text-xs text-text-muted mb-1.5">API Key</label>
                <div className="relative">
                  <input
                    type={showKey ? "text" : "password"}
                    value={keyInput}
                    onChange={(e) => { setKeyInput(e.target.value); setError(""); }}
                    placeholder="prom_..."
                    className="w-full bg-surface border border-border rounded-xl px-3 py-2.5
                               text-sm text-text-primary font-mono placeholder:text-text-dim
                               focus:outline-none focus:border-accent/50 pr-10"
                  />
                  <button
                    onClick={() => setShowKey((v) => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-text-dim hover:text-text-muted"
                  >
                    {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>
              {error && (
                <p className="flex items-center gap-1.5 text-xs text-red-400">
                  <AlertCircle size={12} /> {error}
                </p>
              )}
              <button
                onClick={handleEnterKey}
                disabled={!keyInput}
                className="w-full py-2.5 rounded-xl bg-accent hover:bg-accent/80
                           text-white text-sm font-medium transition-colors
                           disabled:opacity-30 disabled:cursor-not-allowed"
              >
                Connect
              </button>
            </>
          ) : generatedKey ? (
            <>
              <div className="p-3 rounded-xl bg-green-500/5 border border-green-500/20">
                <div className="flex items-center gap-2 mb-2">
                  <CheckCircle size={14} className="text-green-400" />
                  <span className="text-xs text-green-400 font-medium">Key generated successfully</span>
                </div>
                <p className="text-xs text-text-muted mb-2">
                  ⚠️ Copy this key now — it won&apos;t be shown again.
                </p>
                <code className="block text-xs font-mono text-text-primary bg-surface
                                  rounded-lg p-2.5 break-all border border-border">
                  {generatedKey}
                </code>
              </div>
              <button
                onClick={onComplete}
                className="w-full py-2.5 rounded-xl bg-accent hover:bg-accent/80
                           text-white text-sm font-medium transition-colors"
              >
                Start using Prometheus
              </button>
            </>
          ) : (
            <>
              <div>
                <label className="block text-xs text-text-muted mb-1.5">Device name</label>
                <input
                  value={keyName}
                  onChange={(e) => setKeyName(e.target.value)}
                  placeholder="e.g. laptop, phone, server"
                  className="w-full bg-surface border border-border rounded-xl px-3 py-2.5
                             text-sm text-text-primary placeholder:text-text-dim
                             focus:outline-none focus:border-accent/50"
                />
                <p className="text-[10px] text-text-dim mt-1">
                  Requires Prometheus backend running at localhost:8000
                </p>
              </div>
              {error && (
                <p className="flex items-center gap-1.5 text-xs text-red-400">
                  <AlertCircle size={12} /> {error}
                </p>
              )}
              <button
                onClick={handleGenerate}
                disabled={loading || !keyName}
                className="w-full py-2.5 rounded-xl bg-accent hover:bg-accent/80
                           text-white text-sm font-medium transition-colors
                           disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {loading ? "Generating..." : "Generate key"}
              </button>
            </>
          )}
        </div>

        <div className="px-6 pb-5">
          <div className="flex items-center gap-2 p-3 rounded-xl bg-surface border border-border">
            <Key size={13} className="text-text-dim shrink-0" />
            <p className="text-[10px] text-text-dim leading-relaxed">
              Your API key is stored locally in your browser. Prometheus never sends it to any third party.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}