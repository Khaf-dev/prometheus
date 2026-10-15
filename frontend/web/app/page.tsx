"use client";

import { useCallback, useEffect, useState } from "react";
import {
  C, CHROME, SURFACE, ENTER_MS, PILLARS, applyAvailability,
  type Capability, type Pillar,
} from "@/app/lib/pillars";
import {
  getActiveTools, getConversation, hasApiKey,
  type Conversation, type Provider,
} from "@/app/lib/api";
import { SkyMap, Constellation, SideNav, Mark, Chip } from "@/app/components/sky/Sky";
import { ChatSurface } from "@/app/components/sky/ChatSurface";
import { ObservationLog, Library } from "@/app/components/sky/Panels";
import { SetupModal } from "@/app/components/SetupModal";

type Stage = "overview" | "map" | "chat" | "log" | "library";
type Turn = { who: "me" | "prom"; body: string };

export default function Home() {
  const [pillars, setPillars] = useState<Pillar[]>(PILLARS);
  const [stage, setStage] = useState<Stage>("overview");
  const [idx, setIdx] = useState(0);
  const [entering, setEntering] = useState(false);
  const [hoverPillar, setHoverPillar] = useState<number | null>(null);
  const [hoverStar, setHoverStar] = useState<string | null>(null);
  const [focus, setFocus] = useState<Capability | null>(null);
  const [lit, setLit] = useState(false);
  const [needsKey, setNeedsKey] = useState(false);
  const [ready, setReady] = useState(false);

  const [convId, setConvId] = useState<string | null>(null);
  const [seedTurns, setSeedTurns] = useState<Turn[] | undefined>(undefined);

  const pillar = pillars[idx];
  const back = stage === "chat" || stage === "log" || stage === "library";

  useEffect(() => {
    const noKey = !hasApiKey();
    setNeedsKey(noKey);
    setReady(true);
    if (!noKey) loadTools();
    const t = setTimeout(() => setLit(true), 140);
    return () => clearTimeout(t);
  }, []);

  async function loadTools() {
    const active = await getActiveTools();
    // Kalau backend belum punya endpoint /tools, jangan matikan semua bintang —
    // lebih baik anggap aktif daripada peta terlihat mati total.
    setPillars(
      active.length
        ? applyAvailability(active)
        : PILLARS.map((p) => ({ ...p, stars: p.stars.map((s) => ({ ...s, on: true })) }))
    );
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setStage((s) => (s === "chat" ? "map" : s === "overview" ? s : "overview"));
      }
      if (stage === "map") {
        if (e.key === "ArrowLeft") setIdx((i) => (i + pillars.length - 1) % pillars.length);
        if (e.key === "ArrowRight") setIdx((i) => (i + 1) % pillars.length);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [stage, pillars.length]);

  const enter = useCallback((i: number) => {
    setIdx(i); setHoverPillar(null); setEntering(true);
    setTimeout(() => { setStage("map"); setEntering(false); }, ENTER_MS);
  }, []);

  function startChat(star: Capability | null) {
    setFocus(star); setConvId(null); setSeedTurns(undefined); setStage("chat");
  }

  async function openConversation(c: Conversation) {
    try {
      const detail = await getConversation(c.id);
      setSeedTurns(
        detail.messages
          .filter((m) => m.role === "user" || m.role === "assistant")
          .map((m) => ({ who: m.role === "user" ? ("me" as const) : ("prom" as const), body: m.content }))
      );
      setConvId(c.id);
      setFocus(null);
      setStage("chat");
    } catch {
      /* biarkan pengguna tetap di Catatan */
    }
  }

  if (!ready) return null;

  const prev = pillars[(idx + pillars.length - 1) % pillars.length];
  const next = pillars[(idx + 1) % pillars.length];

  return (
    <div className="relative w-full h-screen overflow-hidden select-none"
         style={{ background: C.void, fontFamily: "Inter, system-ui, sans-serif" }}>

      {needsKey && <SetupModal onComplete={() => { setNeedsKey(false); loadTools(); }} />}

      <div className="absolute inset-0 pointer-events-none"
           style={{
             backgroundImage: `radial-gradient(${C.faint} 1px, transparent 1px)`,
             backgroundSize: "26px 26px",
             opacity: back ? 0.25 : 0.9,
             transition: `opacity 700ms ${SURFACE}`,
           }} />
      <div className="absolute inset-0 pointer-events-none"
           style={{ background: `radial-gradient(90% 70% at 50% 45%, ${C.deep} 0%, transparent 70%)` }} />

      <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
           style={{
             width: "min(96vw, 160vh)", height: "min(57.6vw, 96vh)",
             opacity: back ? 0.16 : 1, transition: `opacity 700ms ${SURFACE}`,
           }}>
        {(stage === "overview" || entering) && (
          <SkyMap pillars={pillars} hover={hoverPillar} setHover={setHoverPillar}
                  entering={entering} enteringIdx={idx} lit={lit} onEnter={enter} />
        )}
        {stage === "map" && (
          <Constellation pillar={pillar} hover={hoverStar} setHover={setHoverStar} onStart={startChat} />
        )}
      </div>

      <header className="absolute top-0 left-0 right-0 flex items-start justify-between px-6 py-4 z-20"
              style={{
                opacity: back ? 0 : 1,
                pointerEvents: back ? "none" : "auto",
                transition: `opacity 300ms ${CHROME}`,
              }}>
        <div className="flex items-center gap-2.5"
             style={{ opacity: lit ? 1 : 0, transition: `opacity 800ms ${CHROME}` }}>
          <Mark hue={stage === "map" ? pillar.hue : C.cream} />
          <span style={{
            fontFamily: "'Cormorant Garamond', serif", fontSize: 17, fontWeight: 400,
            letterSpacing: "0.28em", color: C.text,
          }}>PROMETHEUS</span>
        </div>

        <div className="flex items-center gap-2">
          {stage === "overview" && !entering && (
            <>
              <Chip onClick={() => setStage("library")} hue={C.gold} label="Perpustakaan" />
              <Chip onClick={() => setStage("log")} hue={C.cream} label="Catatan" />
            </>
          )}
          {stage === "map" && (
            <Chip onClick={() => setStage("overview")} hue={pillar.hue} label="← Semua wilayah" />
          )}
        </div>
      </header>

      {stage === "map" && (
        <>
          <SideNav side="left" pillar={prev}
                   onClick={() => setIdx((i) => (i + pillars.length - 1) % pillars.length)} />
          <SideNav side="right" pillar={next}
                   onClick={() => setIdx((i) => (i + 1) % pillars.length)} />
        </>
      )}

      <div className="absolute bottom-0 left-0 right-0 z-20 flex flex-col items-center gap-3 pb-6 pt-10"
           style={{
             background: `linear-gradient(to top, ${C.void} 42%, transparent)`,
             opacity: back ? 0 : lit ? 1 : 0,
             pointerEvents: back ? "none" : "auto",
             transition: `opacity 500ms ${CHROME}`,
           }}>
        {stage === "map" ? (
          <>
            <div className="text-center pointer-events-none">
              <div style={{
                fontFamily: "'Cormorant Garamond', serif", fontSize: "clamp(19px,2.2vw,30px)",
                fontWeight: 400, letterSpacing: "0.22em", color: C.text, lineHeight: 1.2,
              }}>{pillar.name.toUpperCase()}</div>
              <div style={{
                fontFamily: "'JetBrains Mono', monospace", fontSize: "clamp(6.5px,.7vw,9.5px)",
                letterSpacing: "0.1em", color: pillar.hue, opacity: 0.7, marginTop: 5,
              }}>{pillar.sub}</div>
            </div>
            <button onClick={() => startChat(null)} className="px-6 py-2.5 rounded-full"
                    style={{
                      background: "transparent", border: `1px solid ${pillar.hue}55`,
                      color: C.text, fontSize: 13.5,
                      transition: `background 200ms ${CHROME}, border-color 200ms ${CHROME}`,
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = `${pillar.hue}1c`; e.currentTarget.style.borderColor = `${pillar.hue}aa`; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.borderColor = `${pillar.hue}55`; }}>
              Mulai percakapan
            </button>
          </>
        ) : (
          <p className="pointer-events-none" style={{
            fontFamily: "'JetBrains Mono', monospace", fontSize: 9.5, letterSpacing: "0.18em",
            color: hoverPillar !== null ? C.text : C.dim, transition: `color 300ms ${CHROME}`,
          }}>
            {hoverPillar !== null ? pillars[hoverPillar].blurb.toUpperCase() : "PILIH SATU WILAYAH"}
          </p>
        )}
      </div>

      {stage === "log" && (
        <ObservationLog onClose={() => setStage("overview")} onOpen={openConversation} />
      )}
      {stage === "library" && <Library onClose={() => setStage("overview")} />}
      {stage === "chat" && (
        <ChatSurface
          pillar={pillar}
          focus={focus}
          provider={(process.env.NEXT_PUBLIC_DEFAULT_PROVIDER as Provider) ?? "ollama"}
          conversationId={convId}
          initialTurns={seedTurns}
          onConversation={setConvId}
          onClose={() => { setFocus(null); setStage("map"); }}
        />
      )}
    </div>
  );
}