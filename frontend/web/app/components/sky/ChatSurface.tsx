"use client";

import { useEffect, useRef, useState } from "react";
import { C, CHROME, SURFACE, labelOfTool, type Capability, type Pillar } from "@/app/lib/pillars";
import { streamChat, type AgentStep, type Provider, type Usage } from "@/app/lib/api";

type Turn = {
  who: "me" | "prom";
  body: string;
  steps?: AgentStep[];
  usage?: Usage;
  error?: string;
  streaming?: boolean;
};

export function ChatSurface({
  pillar, focus, provider, conversationId, initialTurns, onClose, onConversation,
}: {
  pillar: Pillar;
  focus: Capability | null;
  provider?: Provider;
  conversationId: string | null;
  initialTurns?: Turn[];
  onClose: () => void;
  onConversation: (id: string) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>(initialTurns ?? []);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [sendHot, setSendHot] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const ready = draft.trim().length > 0 && !busy;
  const opener = focus ? focus.ask : pillar.prompt;

  useEffect(() => {
    const t = setTimeout(() => inputRef.current?.focus(), 380);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  useEffect(() => () => abortRef.current?.abort(), []);

  async function send(text?: string) {
    const body = (text ?? draft).trim();
    if (!body || busy) return;

    const history = turns
      .filter((t) => !t.error)
      .map((t) => ({ role: t.who === "me" ? ("user" as const) : ("assistant" as const), content: t.body }));

    setTurns((t) => [...t, { who: "me", body }, { who: "prom", body: "", steps: [], streaming: true }]);
    setDraft("");
    setBusy(true);
    abortRef.current = new AbortController();

    await streamChat({
      messages: [...history, { role: "user", content: body }],
      conversationId,
      provider,
      taskType: pillar.taskType,
      agentMode: true,
      pillar: pillar.id,
      focusTool: focus?.tool,
      signal: abortRef.current.signal,

      onStep: (step) =>
        setTurns((t) =>
          t.map((turn, i) =>
            i === t.length - 1 && turn.who === "prom"
              ? { ...turn, steps: [...(turn.steps ?? []), step] }
              : turn
          )
        ),

      onDelta: (delta) =>
        setTurns((t) =>
          t.map((turn, i) =>
            i === t.length - 1 && turn.who === "prom"
              ? { ...turn, body: turn.body + delta }
              : turn
          )
        ),

      onDone: ({ usage, conversationId: cid }) => {
        setTurns((t) =>
          t.map((turn, i) =>
            i === t.length - 1 && turn.who === "prom"
              ? { ...turn, streaming: false, usage }
              : turn
          )
        );
        if (cid) onConversation(cid);
        setBusy(false);
      },

      onError: (message) => {
        setTurns((t) =>
          t.map((turn, i) =>
            i === t.length - 1 && turn.who === "prom"
              ? { ...turn, streaming: false, error: message }
              : turn
          )
        );
        setBusy(false);
      },
    });
  }

  function stop() {
    abortRef.current?.abort();
    setBusy(false);
    setTurns((t) =>
      t
        // Buang balasan yang dihentikan sebelum sempat berisi apa pun —
        // gelembung kosong lebih membingungkan daripada tidak ada sama sekali.
        .filter((x, i) => !(i === t.length - 1 && x.who === "prom" && !x.body.trim()))
        .map((x) => (x.streaming ? { ...x, streaming: false } : x))
    );
  }

  return (
    <div className="absolute inset-0 z-30 flex flex-col"
         style={{ opacity: 0, animation: `appear 380ms ${SURFACE} forwards` }}>
      <div className="flex items-center gap-3 px-5 py-4" style={{ borderBottom: `1px solid ${C.line}55` }}>
        <button onClick={onClose} className="px-3 py-1.5 rounded-full"
                style={{
                  fontFamily: "'JetBrains Mono', monospace", fontSize: 9, letterSpacing: "0.12em",
                  color: C.dim, border: `1px solid ${C.line}88`, background: "transparent",
                  transition: `color 180ms ${CHROME}, border-color 180ms ${CHROME}`,
                }}
                onMouseEnter={(e) => { e.currentTarget.style.color = C.text; e.currentTarget.style.borderColor = `${pillar.hue}88`; }}
                onMouseLeave={(e) => { e.currentTarget.style.color = C.dim; e.currentTarget.style.borderColor = `${C.line}88`; }}>
          ← {pillar.name.toUpperCase()}
        </button>
        <span style={{ width: 6, height: 6, borderRadius: 99, background: pillar.hue, display: "inline-block" }} />
        <span style={{
          fontFamily: "'JetBrains Mono', monospace", fontSize: 9, letterSpacing: "0.12em",
          color: focus ? pillar.hue : C.dim,
        }}>
          {focus ? focus.label.toUpperCase() : `${pillar.stars.filter((s) => s.on).length} KEMAMPUAN SIAP`}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-2xl mx-auto px-5 py-9">
          {turns.length === 0 && (
            <>
              <p style={{
                fontFamily: "'Cormorant Garamond', serif", fontSize: "clamp(24px,3.4vw,32px)",
                fontWeight: 300, color: C.text, lineHeight: 1.3, marginBottom: 20,
              }}>
                {focus ? focus.label : pillar.blurb}
              </p>
              <button onClick={() => send(opener)} className="text-left px-4 py-3 rounded-xl w-full"
                      style={{ background: C.deep, border: `1px solid ${C.line}66`, color: C.text, fontSize: 14, transition: `all 200ms ${CHROME}` }}
                      onMouseEnter={(e) => { e.currentTarget.style.borderColor = `${pillar.hue}66`; }}
                      onMouseLeave={(e) => { e.currentTarget.style.borderColor = `${C.line}66`; }}>
                {opener}
              </button>
            </>
          )}

          <div className="space-y-5">
            {turns.map((t, i) => <Turn key={i} turn={t} hue={pillar.hue} />)}
            <div ref={endRef} />
          </div>
        </div>
      </div>

      <div className="px-5 pb-5">
        <div className="max-w-2xl mx-auto flex items-stretch rounded-2xl"
             style={{ background: `${C.deep}cc`, border: `1px solid ${C.line}77`, backdropFilter: "blur(14px)" }}>
          <textarea ref={inputRef} rows={1} value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                    placeholder={`Tanya soal ${pillar.sub}…`}
                    className="flex-1 bg-transparent resize-none focus:outline-none px-4 py-3.5"
                    style={{ color: C.text, fontSize: 14.5, lineHeight: 1.55, maxHeight: 140 }} />
          <button onClick={() => (busy ? stop() : send())} disabled={!busy && !ready}
                  onMouseEnter={() => setSendHot(true)} onMouseLeave={() => setSendHot(false)}
                  className="flex items-center gap-2 px-4 my-2"
                  style={{
                    borderLeft: `1px solid ${(ready || busy) && sendHot ? `${pillar.hue}66` : `${C.line}77`}`,
                    background: "transparent", cursor: ready || busy ? "pointer" : "default",
                    transition: `border-color 180ms ${CHROME}`,
                  }}>
            <span style={{
              fontSize: 15, fontWeight: 500,
              color: busy ? "#E0847C" : ready ? (sendHot ? pillar.hue : C.text) : C.dim,
              transition: `color 180ms ${CHROME}`,
            }}>
              {busy ? "Hentikan" : "Kirim"}
            </span>
            {!busy && <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, color: C.dim }}>↵</span>}
          </button>
        </div>
      </div>
    </div>
  );
}

/* Satu giliran percakapan */
function Turn({ turn, hue }: { turn: Turn; hue: string }) {
  const mine = turn.who === "me";
  if (mine) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] px-4 py-3 rounded-2xl"
             style={{ background: `${hue}1a`, border: `1px solid ${hue}3a`, color: C.text, fontSize: 14.5, lineHeight: 1.6 }}>
          {turn.body}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[88%] w-full">
        {turn.steps && turn.steps.length > 0 && <Steps steps={turn.steps} hue={hue} />}

        {turn.error ? (
          <div className="px-4 py-3 rounded-2xl"
               style={{ background: "#2A1520", border: "1px solid #E0847C55", color: "#E0A79C", fontSize: 13.5, lineHeight: 1.6 }}>
            {turn.error}
          </div>
        ) : turn.body ? (
          <div className="px-4 py-3 rounded-2xl"
               style={{ background: C.deep, border: `1px solid ${C.line}66`, color: C.text, fontSize: 14.5, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
            {turn.body}
            {turn.streaming && <Caret hue={hue} />}
          </div>
        ) : turn.streaming ? (
          <Working hue={hue} />
        ) : null}

        {turn.usage && turn.usage.tokens_output > 0 && (
          <div className="mt-1.5 px-1" style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, color: C.dim }}>
            {turn.usage.tokens_output} token
            {turn.usage.cost_usd > 0 && ` · $${turn.usage.cost_usd.toFixed(5)}`}
          </div>
        )}
      </div>
    </div>
  );
}

/* Langkah agent — daftar rapat, bukan kartu bertumpuk */
function Steps({ steps, hue }: { steps: AgentStep[]; hue: string }) {
  const [open, setOpen] = useState(false);
  const tools = steps.filter((s) => s.type === "tool_call");

  return (
    <div className="mb-2">
      <button onClick={() => setOpen((v) => !v)}
              className="flex items-center gap-2"
              style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, letterSpacing: "0.1em", color: C.dim }}>
        <span style={{ width: 3.5, height: 3.5, borderRadius: 99, background: hue, display: "inline-block" }} />
        {tools.length > 0
          ? tools.map((s) => labelOfTool(s.tool_name ?? "").toUpperCase()).join(" · ")
          : `${steps.length} LANGKAH`}
        <span style={{ opacity: 0.6 }}>{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="mt-2 pl-3 space-y-1" style={{ borderLeft: `1px solid ${C.line}66` }}>
          {steps.map((s, i) => (
            <div key={i} style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9.5, color: C.dim, lineHeight: 1.5 }}>
              <span style={{ color: s.type === "tool_call" ? hue : C.dim }}>{s.type}</span>
              {" · "}
              {s.content.length > 120 ? `${s.content.slice(0, 120)}…` : s.content}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Caret({ hue }: { hue: string }) {
  return (
    <span style={{
      display: "inline-block", width: 2, height: "1em", marginLeft: 2,
      background: hue, verticalAlign: "text-bottom",
      animation: "blink 1s steps(2) infinite",
    }} />
  );
}

function Working({ hue }: { hue: string }) {
  return (
    <div className="flex items-center gap-2 px-1 py-2">
      <div style={{
        height: 1, width: 14,
        background: `linear-gradient(90deg, ${hue}, transparent)`,
        animation: "sweep 1.6s ease-in-out infinite",
      }} />
      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9.5, letterSpacing: "0.14em", color: C.dim }}>
        MENELUSURI
      </span>
    </div>
  );
}