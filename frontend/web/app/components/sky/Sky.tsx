"use client";

import { useMemo, useState } from "react";
import * as Icons from "lucide-react";
import {
  C, CAMERA, CHROME, SURFACE, CORE, MAG_O, MAG_R,
  type Capability, type Pillar,
} from "@/app/lib/pillars";

/** Ambil komponen ikon lucide dari nama string. */
function Glyph({ name, ...rest }: { name: string } & Record<string, unknown>) {
  const Cmp = (Icons as unknown as Record<string, React.ComponentType<Record<string, unknown>>>)[name]
    ?? Icons.Circle;
  return <Cmp {...rest} />;
}

/* ══════════ Peta wilayah ══════════ */
export function SkyMap({
  pillars, hover, setHover, entering, enteringIdx, lit, onEnter,
}: {
  pillars: Pillar[];
  hover: number | null;
  setHover: (i: number | null) => void;
  entering: boolean;
  enteringIdx: number;
  lit: boolean;
  onEnter: (i: number) => void;
}) {
  const dust = useMemo(() => {
    const hues = [C.flare, C.violet, C.gold, C.cream, "#E0847C", "#7FD8C0"];
    return Array.from({ length: 88 }, () => {
      const a = Math.random() * Math.PI * 2;
      const r = Math.pow(Math.random(), 1.9) * 54;
      return {
        x: CORE.x + Math.cos(a) * r * 1.25,
        y: CORE.y + Math.sin(a) * r,
        s: Math.random() * 2 + 0.7,
        c: hues[(Math.random() * hues.length) | 0],
        o: 0.25 + Math.random() * 0.6,
      };
    });
  }, []);

  const target = pillars[enteringIdx];

  return (
    <div className="absolute inset-0"
         style={{
           opacity: lit ? 1 : 0,
           transform: entering ? "scale(1.5)" : "scale(1)",
           transformOrigin: entering ? `${target.hub.x / 10}% ${target.hub.y / 6}%` : "50% 50%",
           transition: `opacity ${entering ? 420 : 900}ms ${SURFACE}, transform 780ms ${CAMERA}`,
           pointerEvents: entering ? "none" : "auto",
         }}>
      <svg viewBox="0 0 1000 600" preserveAspectRatio="none" className="absolute inset-0 w-full h-full">
        {[110, 190, 272].map((r) => (
          <ellipse key={r} cx={CORE.x} cy={CORE.y} rx={r * 1.35} ry={r}
                   fill="none" stroke={C.line} strokeOpacity="0.28" strokeWidth="0.6" />
        ))}
        {pillars.map((p) => (
          <line key={p.id} x1={CORE.x} y1={CORE.y} x2={p.hub.x} y2={p.hub.y}
                stroke={C.line} strokeWidth="0.8" strokeDasharray="2 6" strokeOpacity="0.55" />
        ))}
        {pillars.map((p, pi) => {
          const dimmed = hover !== null && hover !== pi;
          const at = (i: number) => (i === -1 ? p.hub : p.mini[i]);
          return (
            <g key={p.id} style={{ opacity: dimmed ? 0.22 : 1, transition: `opacity 340ms ${CHROME}` }}>
              {p.miniEdges.map(([a, b], i) => {
                const A = at(a), B = at(b);
                return <line key={i} x1={A.x} y1={A.y} x2={B.x} y2={B.y}
                             stroke={C.line} strokeWidth="0.9" strokeOpacity="0.8" />;
              })}
              {p.mini.map((m, i) => <circle key={i} cx={m.x} cy={m.y} r="3" fill={C.cream} opacity="0.85" />)}
            </g>
          );
        })}
        {dust.map((d, i) => <circle key={i} cx={d.x} cy={d.y} r={d.s} fill={d.c} opacity={d.o} />)}
      </svg>

      {pillars.map((p, pi) => {
        const hot = hover === pi;
        const dimmed = hover !== null && !hot;
        const anchorTop = p.labelAnchor === "top";
        const live = p.stars.filter((s) => s.on).length;
        return (
          <div key={p.id}>
            <button onClick={() => onEnter(pi)}
                    onMouseEnter={() => setHover(pi)} onMouseLeave={() => setHover(null)}
                    aria-label={`Buka wilayah ${p.name}`}
                    className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full flex items-center justify-center"
                    style={{
                      left: `${p.hub.x / 10}%`, top: `${p.hub.y / 6}%`,
                      width: hot ? 42 : 36, height: hot ? 42 : 36,
                      border: `1px solid ${p.hue}${hot ? "" : "88"}`,
                      background: hot ? `${p.hue}1a` : "transparent",
                      boxShadow: hot ? `0 0 24px ${p.hue}44` : "none",
                      opacity: dimmed ? 0.3 : 1, transition: `all 340ms ${SURFACE}`,
                    }}>
              <Glyph name={p.icon} size={hot ? 16 : 14} color={p.hue} strokeWidth={1.4} />
            </button>

            <div className="absolute -translate-x-1/2 text-center pointer-events-none"
                 style={{
                   left: `${p.label.x / 10}%`,
                   [anchorTop ? "top" : "bottom"]: anchorTop
                     ? `${p.label.y / 6}%` : `${100 - p.label.y / 6}%`,
                   opacity: dimmed ? 0.3 : 1, transition: `opacity 340ms ${CHROME}`,
                 }}>
              <div style={{
                fontFamily: "'Cormorant Garamond', serif", fontSize: "clamp(16px,1.9vw,28px)",
                fontWeight: 400, letterSpacing: "0.2em", color: C.text, whiteSpace: "nowrap", lineHeight: 1.2,
              }}>{p.name.toUpperCase()}</div>
              <div style={{
                fontFamily: "'JetBrains Mono', monospace", fontSize: "clamp(6.5px,.68vw,9.5px)",
                letterSpacing: "0.08em", color: p.hue, opacity: 0.65, marginTop: 5, whiteSpace: "nowrap",
              }}>
                {live > 0 ? p.sub : "belum tersambung"}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ══════════ Rasi lengkap ══════════ */
export function Constellation({
  pillar, hover, setHover, onStart,
}: {
  pillar: Pillar;
  hover: string | null;
  setHover: (id: string | null) => void;
  onStart: (star: Capability | null) => void;
}) {
  const ordered = pillar.trace
    .map((id) => pillar.stars.find((s) => s.id === id))
    .filter(Boolean) as Capability[];
  const pts = [...ordered, ordered[0]];
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" ");

  const { total, arriveAt } = useMemo(() => {
    let acc = 0;
    const arrive = [0];
    for (let i = 1; i < pts.length; i++) {
      acc += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
      arrive.push(acc);
    }
    return { total: acc, arriveAt: arrive };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pillar.id]);

  const TRACE_MS = 1400, START = 140;

  return (
    <div className="absolute inset-0" style={{ animation: `emerge 600ms ${SURFACE} both` }}>
      <div className="absolute inset-0 flex items-center justify-center pointer-events-none overflow-hidden">
        <span style={{
          fontFamily: "'Cormorant Garamond', serif", fontSize: "clamp(52px,9vw,140px)",
          fontWeight: 300, letterSpacing: "0.04em", color: C.text, opacity: 0.038, whiteSpace: "nowrap",
        }}>{pillar.name.toUpperCase()}</span>
      </div>

      <svg viewBox="0 0 1000 600" preserveAspectRatio="none" className="absolute inset-0 w-full h-full">
        <defs>
          <radialGradient id={`h-${pillar.id}`}>
            <stop offset="0%" stopColor={pillar.hue} stopOpacity="0.5" />
            <stop offset="55%" stopColor={pillar.hue} stopOpacity="0.1" />
            <stop offset="100%" stopColor={pillar.hue} stopOpacity="0" />
          </radialGradient>
        </defs>

        {[140, 226, 300].map((r) => (
          <ellipse key={r} cx={500} cy={306} rx={r * 1.4} ry={r}
                   fill="none" stroke={C.line} strokeOpacity="0.2" strokeWidth="0.6" />
        ))}

        <path d={d} fill="none" stroke={C.line} strokeWidth="1.1" opacity="0.9"
              strokeLinecap="round" strokeLinejoin="round"
              style={{
                strokeDasharray: total, strokeDashoffset: total,
                animation: `trace ${TRACE_MS}ms ${CAMERA} ${START}ms forwards`,
              }} />

        {ordered.map((s, i) => {
          const hot = hover === s.id;
          const delay = START + (arriveAt[i] / total) * TRACE_MS;
          const R = s.on ? MAG_R[s.mag] : 2.8;
          const spike = hot ? 16 : 10;
          return (
            <g key={s.id} style={{
              opacity: 0, animation: `ignite 420ms ${SURFACE} ${delay}ms forwards`,
              transformOrigin: `${s.x}px ${s.y}px`,
            }}>
              {s.on && (
                <circle cx={s.x} cy={s.y} r={hot ? 28 : 19} fill={`url(#h-${pillar.id})`}
                        style={{ transition: `r 340ms ${SURFACE}` }} />
              )}
              {s.on && ([[1, 0], [0, 1]] as const).map(([dx, dy], k) => (
                <line key={k}
                      x1={s.x - dx * spike} y1={s.y - dy * spike}
                      x2={s.x + dx * spike} y2={s.y + dy * spike}
                      stroke={C.cream} strokeWidth="0.7" strokeLinecap="round"
                      opacity={hot ? 0.55 : 0.28}
                      style={{ transition: `all 340ms ${SURFACE}` }} />
              ))}
              <circle cx={s.x} cy={s.y} r={hot ? R + 1.6 : R}
                      fill={s.on ? C.cream : "transparent"}
                      stroke={s.on ? "none" : C.dim} strokeWidth="1"
                      strokeDasharray={s.on ? "0" : "2 2"}
                      opacity={s.on ? MAG_O[s.mag] : 0.6}
                      style={{ transition: `r 340ms ${SURFACE}` }} />
            </g>
          );
        })}
      </svg>

      {ordered.map((s, i) => {
        const hot = hover === s.id;
        const delay = START + (arriveAt[i] / total) * TRACE_MS;
        return (
          <div key={s.id} className="absolute -translate-x-1/2 -translate-y-1/2"
               style={{
                 left: `${s.x / 10}%`, top: `${s.y / 6}%`, opacity: 0,
                 animation: `ignite 420ms ${SURFACE} ${delay + 90}ms forwards`,
               }}>
            <button onClick={() => s.on && onStart(s)}
                    onMouseEnter={() => setHover(s.id)} onMouseLeave={() => setHover(null)}
                    disabled={!s.on}
                    aria-label={s.on ? `Mulai percakapan: ${s.label}` : `${s.label} belum tersedia`}
                    className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full"
                    style={{ width: 44, height: 44, background: "transparent", cursor: s.on ? "pointer" : "default" }} />

            <div className="absolute left-1/2 -translate-x-1/2 pointer-events-none" style={{ bottom: 13 }}>
              <Glyph name={s.icon} size={hot ? 15 : 13} strokeWidth={1.3}
                     color={s.on ? (hot ? C.cream : pillar.hue) : C.dim}
                     style={{ opacity: s.on ? (hot ? 1 : 0.75) : 0.4, transition: `all 220ms ${CHROME}` }} />
            </div>

            <div className="absolute left-1/2 -translate-x-1/2 text-center pointer-events-none"
                 style={{ top: 15, whiteSpace: "nowrap" }}>
              <div style={{
                fontSize: "clamp(9.5px,1vw,13.5px)", fontWeight: 500,
                color: s.on ? C.text : C.dim, opacity: s.on ? (hot ? 1 : 0.84) : 0.5,
                transition: `opacity 200ms ${CHROME}`,
              }}>{s.label}</div>
              <div style={{
                fontFamily: "'JetBrains Mono', monospace", fontSize: "clamp(6.5px,.66vw,9px)",
                color: s.on ? pillar.hue : C.dim, opacity: hot ? 0.85 : 0,
                transition: `opacity 200ms ${CHROME}`, marginTop: 2,
              }}>{s.on ? s.tool : "belum tersedia"}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ══════════ Navigasi tepi ══════════ */
export function SideNav({ side, pillar, onClick }: {
  side: "left" | "right"; pillar: Pillar; onClick: () => void;
}) {
  const [hot, setHot] = useState(false);
  const Arrow = side === "left" ? Icons.ChevronLeft : Icons.ChevronRight;
  return (
    <button onClick={onClick} onMouseEnter={() => setHot(true)} onMouseLeave={() => setHot(false)}
            aria-label={`Ke wilayah ${pillar.name}`}
            className="absolute top-1/2 -translate-y-1/2 z-20 flex items-center gap-1.5 py-8"
            style={{
              [side]: 0,
              paddingLeft: side === "left" ? 8 : 14,
              paddingRight: side === "right" ? 8 : 14,
              background: `linear-gradient(to ${side === "left" ? "right" : "left"}, ${C.void}ee, transparent)`,
            }}>
      {side === "left" && <Arrow size={16} color={hot ? C.text : C.dim} strokeWidth={1.2} />}
      <span style={{
        writingMode: "vertical-rl", transform: side === "left" ? "rotate(180deg)" : "none",
        fontFamily: "'Cormorant Garamond', serif", fontSize: 13, letterSpacing: "0.24em",
        color: hot ? C.text : C.dim, transition: `color 200ms ${CHROME}`,
      }}>{pillar.name.toUpperCase()}</span>
      {side === "right" && <Arrow size={16} color={hot ? C.text : C.dim} strokeWidth={1.2} />}
    </button>
  );
}

/* ══════════ Tanda gambar ══════════ */
export function Mark({ hue }: { hue: string }) {
  return (
    <svg width="20" height="24" viewBox="0 0 22 26" fill="none">
      <path d="M11 25V9" stroke={hue} strokeWidth="1.4" strokeLinecap="round" />
      <path d="M11 13C11 13 6 12 4 8C2 4 3 1 3 1C3 1 8 3 10 7C11.4 9.8 11 13 11 13Z" stroke={hue} strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M11 15C11 15 16 14 18 10C20 6 19 3 19 3C19 3 14 5 12 9C10.6 11.8 11 15 11 15Z" stroke={hue} strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

export function Chip({ onClick, hue, label }: { onClick: () => void; hue: string; label: string }) {
  return (
    <button onClick={onClick} className="px-3 py-1.5 rounded-full"
            style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: 9, letterSpacing: "0.12em",
              color: C.dim, border: `1px solid ${C.line}88`, background: `${C.void}aa`,
              transition: `color 180ms ${CHROME}, border-color 180ms ${CHROME}`,
            }}
            onMouseEnter={(e) => { e.currentTarget.style.color = C.text; e.currentTarget.style.borderColor = `${hue}88`; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = C.dim; e.currentTarget.style.borderColor = `${C.line}88`; }}>
      {label.toUpperCase()}
    </button>
  );
}