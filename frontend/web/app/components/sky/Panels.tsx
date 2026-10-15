"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as Icons from "lucide-react";
import { C, CHROME, SURFACE, PILLARS } from "@/app/lib/pillars";
import {
  listConversations, deleteConversation,
  listDocuments, uploadDocuments, deleteDocument, knowledgeStats, clipUrl,
  obsidianStatus, syncObsidian,
  type Conversation, type DocumentItem, type UploadResult,
} from "@/app/lib/api";
import { Chip } from "./Sky";

/* Kelompokkan percakapan per hari — seperti buku catatan pengamatan */
function groupByDay(items: Conversation[]) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const yest = new Date(today); yest.setDate(yest.getDate() - 1);

  const map = new Map<string, Conversation[]>();
  for (const c of items) {
    const d = new Date(c.updated_at);
    const day = new Date(d); day.setHours(0, 0, 0, 0);
    const key =
      day.getTime() === today.getTime() ? "Hari ini"
      : day.getTime() === yest.getTime() ? "Kemarin"
      : d.toLocaleDateString("id-ID", { day: "numeric", month: "short" });
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(c);
  }
  return [...map.entries()];
}

function hhmm(iso: string) {
  return new Date(iso).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
}

/* ══════════ Catatan pengamatan ══════════ */
export function ObservationLog({ onClose, onOpen }: {
  onClose: () => void;
  onOpen: (c: Conversation) => void;
}) {
  const [items, setItems] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    listConversations()
      .then(setItems)
      .catch(() => setFailed(true))
      .finally(() => setLoading(false));
  }, []);

  const days = useMemo(() => groupByDay(items), [items]);

  async function remove(e: React.MouseEvent, id: string) {
    e.stopPropagation();
    setItems((xs) => xs.filter((x) => x.id !== id));
    try { await deleteConversation(id); } catch { /* biarkan; daftar akan segar saat dibuka lagi */ }
  }

  return (
    <Overlay title="Catatan"
             sub={loading ? "MEMUAT…" : `${items.length} PERCAKAPAN TERSIMPAN`}
             onClose={onClose}>
      {failed && <Empty text="Tidak bisa mengambil catatan. Pastikan server berjalan." />}
      {!failed && !loading && items.length === 0 && (
        <Empty text="Belum ada percakapan. Mulai dari salah satu wilayah." />
      )}

      {days.map(([day, entries]) => (
        <section key={day} className="mb-8">
          <div className="flex items-baseline gap-3 mb-1">
            <h3 style={{
              fontFamily: "'Cormorant Garamond', serif", fontSize: 17, fontWeight: 400,
              letterSpacing: "0.16em", color: C.text, opacity: 0.7,
            }}>{day.toUpperCase()}</h3>
            <div className="flex-1" style={{ height: 1, background: `${C.line}66` }} />
          </div>

          {entries.map((c) => {
            const p = PILLARS.find((x) => x.id === c.provider) ?? null;
            const hue = p?.hue ?? C.cream;
            const hot = hoverId === c.id;
            return (
              <div key={c.id}
                   onMouseEnter={() => setHoverId(c.id)} onMouseLeave={() => setHoverId(null)}
                   className="w-full flex items-center gap-3 py-3"
                   style={{ borderBottom: `1px solid ${C.line}44`, background: hot ? C.deep : "transparent", transition: `background 160ms ${CHROME}` }}>
                <button onClick={() => onOpen(c)} className="flex items-center gap-3 flex-1 min-w-0 text-left">
                  <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10.5, color: hot ? C.text : C.dim, width: 40, flexShrink: 0 }}>
                    {hhmm(c.updated_at)}
                  </span>
                  <span style={{
                    width: 5, height: 5, borderRadius: 99, background: hue, flexShrink: 0,
                    opacity: hot ? 1 : 0.6, boxShadow: hot ? `0 0 8px ${hue}` : "none", transition: "all 160ms",
                  }} />
                  <span className="flex-1 truncate" style={{ fontSize: 14, color: hot ? C.text : `${C.text}bb` }}>
                    {c.title ?? "Tanpa judul"}
                  </span>
                </button>
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9.5, color: C.dim, width: 28, textAlign: "right", flexShrink: 0 }}>
                  {c.message_count}
                </span>
                <button onClick={(e) => remove(e, c.id)} aria-label="Hapus percakapan"
                        className="shrink-0 p-1 rounded"
                        style={{ opacity: hot ? 0.7 : 0, transition: `opacity 160ms ${CHROME}` }}
                        onMouseEnter={(e) => (e.currentTarget.style.opacity = "1")}>
                  <Icons.Trash2 size={12} color="#E0847C" />
                </button>
              </div>
            );
          })}
        </section>
      ))}
    </Overlay>
  );
}

/* ══════════ Perpustakaan — unggah & kelola dokumen ══════════ */
const ACCEPT = ".pdf,.md,.markdown,.mdx,.txt,.text,.log,.py,.js,.ts,.tsx,.jsx,.java,.go,.rs,.c,.cpp,.cs,.rb,.php,.sql,.sh,.yaml,.yml,.json,.html,.css";

export function Library({ onClose }: { onClose: () => void }) {
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [stats, setStats] = useState<{ total_documents: number; total_chunks: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [results, setResults] = useState<UploadResult[] | null>(null);
  const [url, setUrl] = useState("");
  const [clipping, setClipping] = useState(false);
  const [vault, setVault] = useState<{ configured: boolean; vault: string | null } | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function refresh() {
    try {
      const [d, s] = await Promise.all([listDocuments(), knowledgeStats()]);
      setDocs(d); setStats(s);
    } catch { /* biarkan kosong */ }
  }
  useEffect(() => { refresh(); obsidianStatus().then(setVault); }, []);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy(true); setResults(null);
    try {
      const res = await uploadDocuments(Array.from(files));
      setResults(res.results);
      await refresh();
    } catch (e) {
      setResults([{
        filename: "Unggahan", document_id: null, status: "failed", chunk_count: 0,
        error: e instanceof Error ? e.message : "Gagal mengunggah",
      }]);
    } finally {
      setBusy(false);
    }
  }

  async function sync() {
    if (syncing) return;
    setSyncing(true); setSyncMsg(null); setResults(null);
    try {
      const r = await syncObsidian();
      if (r.status !== "ok") {
        setSyncMsg(r.error ?? "Sinkronisasi gagal.");
      } else {
        const bagian: string[] = [];
        if (r.added) bagian.push(`${r.added} baru`);
        if (r.updated) bagian.push(`${r.updated} diperbarui`);
        if (r.skipped) bagian.push(`${r.skipped} tidak berubah`);
        if (r.failed) bagian.push(`${r.failed} gagal`);
        setSyncMsg(bagian.length ? bagian.join(" · ") : "Semua catatan sudah mutakhir.");
      }
      await refresh();
    } catch (e) {
      setSyncMsg(e instanceof Error ? e.message : "Sinkronisasi gagal.");
    } finally {
      setSyncing(false);
    }
  }

  async function clip() {
    const target = url.trim();
    if (!target || clipping) return;
    setClipping(true); setResults(null);
    try {
      const r = await clipUrl(target);
      setResults([{
        filename: r.title, document_id: r.document_id,
        status: r.status, chunk_count: r.chunk_count, error: r.error,
      }]);
      if (r.status === "ready") setUrl("");
      await refresh();
    } catch (e) {
      setResults([{
        filename: target, document_id: null, status: "failed", chunk_count: 0,
        error: e instanceof Error ? e.message : "Gagal menyimpan halaman",
      }]);
    } finally {
      setClipping(false);
    }
  }

  async function remove(id: string) {
    setDocs((xs) => xs.filter((x) => x.id !== id));
    try { await deleteDocument(id); await refresh(); } catch { /* abaikan */ }
  }

  return (
    <Overlay title="Perpustakaan"
             sub={stats ? `${stats.total_documents} DOKUMEN · ${stats.total_chunks} POTONGAN` : "MEMUAT…"}
             onClose={onClose}>
      {/* Zona unggah */}
      <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
           onDragLeave={() => setDrag(false)}
           onDrop={(e) => { e.preventDefault(); setDrag(false); handleFiles(e.dataTransfer.files); }}
           onClick={() => fileRef.current?.click()}
           className="rounded-2xl px-6 py-9 mb-7 text-center cursor-pointer"
           style={{
             border: `1px dashed ${drag ? C.gold : `${C.line}aa`}`,
             background: drag ? `${C.gold}0d` : `${C.deep}88`,
             transition: `all 200ms ${CHROME}`,
           }}>
        <input ref={fileRef} type="file" multiple accept={ACCEPT} className="hidden"
               onChange={(e) => handleFiles(e.target.files)} />
        <Icons.UploadCloud size={22} color={drag ? C.gold : C.dim} strokeWidth={1.3} className="mx-auto mb-3" />
        <p style={{ fontSize: 14.5, color: C.text, marginBottom: 6 }}>
          {busy ? "Sedang memproses…" : "Jatuhkan berkas di sini, atau klik untuk memilih"}
        </p>
        <p style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9, letterSpacing: "0.1em", color: C.dim }}>
          PDF · MARKDOWN · TEKS · BERKAS KODE · MAKS 10 MB
        </p>
      </div>

      {/* Simpan halaman web — pintu masuk lain ke pipeline yang sama */}
      <div className="flex items-stretch rounded-xl mb-7"
           style={{ background: `${C.deep}88`, border: `1px solid ${C.line}88` }}>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") clip(); }}
          placeholder="Tempel tautan artikel untuk disimpan…"
          className="flex-1 bg-transparent focus:outline-none px-4 py-3"
          style={{ color: C.text, fontSize: 14 }}
        />
        <button onClick={clip} disabled={!url.trim() || clipping}
                className="flex items-center gap-2 px-4 my-1.5"
                style={{
                  borderLeft: `1px solid ${C.line}88`, background: "transparent",
                  cursor: url.trim() && !clipping ? "pointer" : "default",
                }}>
          <Icons.Link2 size={13} color={url.trim() && !clipping ? C.gold : C.dim} strokeWidth={1.5} />
          <span style={{
            fontSize: 14, fontWeight: 500,
            color: url.trim() && !clipping ? C.text : C.dim,
            transition: `color 180ms ${CHROME}`,
          }}>
            {clipping ? "Menyimpan…" : "Simpan"}
          </span>
        </button>
      </div>

      {/* Obsidian — hanya tampil kalau vault sudah diatur di .env */}
      {vault?.configured && (
        <div className="flex items-center gap-3 rounded-xl px-4 py-3 mb-7"
             style={{ background: `${C.deep}66`, border: `1px solid ${C.line}66` }}>
          <Icons.NotebookPen size={15} color={C.gold} strokeWidth={1.4} />
          <div className="flex-1 min-w-0">
            <div style={{ fontSize: 13.5, color: C.text }}>Vault Obsidian</div>
            <div className="truncate" style={{
              fontFamily: "'JetBrains Mono', monospace", fontSize: 9,
              color: C.dim, marginTop: 2,
            }}>
              {syncMsg ?? vault.vault}
            </div>
          </div>
          <button onClick={sync} disabled={syncing}
                  className="px-3.5 py-1.5 rounded-full shrink-0"
                  style={{
                    border: `1px solid ${syncing ? `${C.line}88` : `${C.gold}66`}`,
                    background: "transparent",
                    color: syncing ? C.dim : C.text,
                    fontSize: 12.5,
                    cursor: syncing ? "default" : "pointer",
                    transition: `all 180ms ${CHROME}`,
                  }}>
            {syncing ? "Menyinkronkan…" : "Sinkronkan"}
          </button>
        </div>
      )}

      {/* Hasil unggahan terakhir */}
      {results && (
        <div className="mb-7 space-y-1">
          {results.map((r, i) => (
            <div key={i} className="flex items-center gap-2.5 py-1.5"
                 style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}>
              <span style={{ color: r.status === "ready" ? "#7FD8C0" : "#E0847C" }}>
                {r.status === "ready" ? "✓" : "✕"}
              </span>
              <span style={{ color: C.text, flex: 1 }} className="truncate">{r.filename}</span>
              <span style={{ color: C.dim }}>
                {r.status === "ready" ? `${r.chunk_count} potongan` : r.error}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Daftar dokumen */}
      {docs.length === 0
        ? <Empty text="Perpustakaan masih kosong. Unggah dokumen agar bisa dicari lewat Second Brain." />
        : (
          <>
            <div className="flex items-baseline gap-3 mb-1">
              <h3 style={{
                fontFamily: "'Cormorant Garamond', serif", fontSize: 17, fontWeight: 400,
                letterSpacing: "0.16em", color: C.text, opacity: 0.7,
              }}>TERSIMPAN</h3>
              <div className="flex-1" style={{ height: 1, background: `${C.line}66` }} />
            </div>
            {docs.map((d) => {
              const hot = hoverId === d.id;
              const tone = d.status === "ready" ? "#7FD8C0" : d.status === "failed" ? "#E0847C" : C.gold;
              return (
                <div key={d.id}
                     onMouseEnter={() => setHoverId(d.id)} onMouseLeave={() => setHoverId(null)}
                     className="flex items-center gap-3 py-3"
                     style={{ borderBottom: `1px solid ${C.line}44`, background: hot ? C.deep : "transparent", transition: `background 160ms ${CHROME}` }}>
                  <span style={{ width: 5, height: 5, borderRadius: 99, background: tone, flexShrink: 0, opacity: hot ? 1 : 0.7 }} />
                  <span className="flex-1 truncate" style={{ fontSize: 14, color: hot ? C.text : `${C.text}bb` }}>
                    {d.title}
                  </span>
                  <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9.5, color: C.dim, flexShrink: 0 }}>
                    {d.source_type}
                  </span>
                  <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 9.5, color: C.dim, width: 40, textAlign: "right", flexShrink: 0 }}>
                    {d.status === "ready" ? d.chunk_count : d.status}
                  </span>
                  <button onClick={() => remove(d.id)} aria-label="Hapus dokumen"
                          className="shrink-0 p-1 rounded"
                          style={{ opacity: hot ? 0.7 : 0, transition: `opacity 160ms ${CHROME}` }}>
                    <Icons.Trash2 size={12} color="#E0847C" />
                  </button>
                </div>
              );
            })}
          </>
        )}
    </Overlay>
  );
}

/* ══════════ Kerangka bersama ══════════ */
function Overlay({ title, sub, onClose, children }: {
  title: string; sub: string; onClose: () => void; children: React.ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="absolute inset-0 z-30 flex flex-col"
         style={{ opacity: 0, animation: `appear 340ms ${SURFACE} forwards`, background: `${C.void}ee`, backdropFilter: "blur(10px)" }}>
      <div className="flex items-center justify-between px-6 py-5">
        <div>
          <h2 style={{
            fontFamily: "'Cormorant Garamond', serif", fontSize: 26, fontWeight: 400,
            letterSpacing: "0.24em", color: C.text,
          }}>{title.toUpperCase()}</h2>
          <p style={{
            fontFamily: "'JetBrains Mono', monospace", fontSize: 9,
            letterSpacing: "0.14em", color: C.dim, marginTop: 4,
          }}>{sub}</p>
        </div>
        <Chip onClick={onClose} hue={C.cream} label="Tutup" />
      </div>
      <div className="flex-1 overflow-y-auto px-6 pb-10">
        <div className="max-w-3xl mx-auto">{children}</div>
      </div>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <p className="py-12 text-center" style={{ fontSize: 13.5, color: C.dim, lineHeight: 1.7 }}>
      {text}
    </p>
  );
}