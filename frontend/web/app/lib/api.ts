/**
 * Prometheus API client.
 * Auth: API key disimpan di localStorage, dikirim sebagai Bearer token.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Role = "user" | "assistant" | "system";
export type Provider = "claude" | "gpt4o" | "gemini" | "ollama";
export type TaskType = "coding" | "research" | "quick" | "general";

export interface AgentStep {
  type: "thinking" | "tool_call" | "tool_result" | "response" | "error";
  content: string;
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface Usage {
  tokens_input: number;
  tokens_output: number;
  cost_usd: number;
  model: string;
}

export interface Conversation {
  id: string;
  title: string | null;
  provider: Provider | null;
  is_archived: boolean;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  role: Role;
  content: string;
  provider?: Provider;
  tokens_input?: number;
  tokens_output?: number;
  cost_usd?: number;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface DocumentItem {
  id: string;
  title: string;
  source_type: string;
  namespace: string | null;
  status: "processing" | "ready" | "failed";
  chunk_count: number;
  error_message: string | null;
  created_at: string;
}

export interface UploadResult {
  filename: string;
  document_id: string | null;
  status: string;
  chunk_count: number;
  error: string | null;
}

// ─── Auth ─────────────────────────────────────────────────────────────────────

const KEY = "prometheus_api_key";

export function getApiKey(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(KEY) ?? "";
}
export function setApiKey(k: string) { localStorage.setItem(KEY, k); }
export function clearApiKey() { localStorage.removeItem(KEY); }
export function hasApiKey(): boolean { return Boolean(getApiKey()); }

function headers(json = true): HeadersInit {
  const k = getApiKey();
  return {
    ...(json ? { "Content-Type": "application/json" } : {}),
    ...(k ? { Authorization: `Bearer ${k}` } : {}),
  };
}

export async function setupApiKey(keyName: string) {
  const res = await fetch(`${API_BASE}/api/v1/auth/setup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key_name: keyName }),
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.detail ?? `Setup gagal: ${res.status}`);
  }
  return res.json() as Promise<{ api_key: string; key_preview: string }>;
}

// ─── Tools & providers ────────────────────────────────────────────────────────

/**
 * Daftar tool yang benar-benar aktif di backend.
 * Dipakai untuk menyalakan/meredupkan bintang di peta wilayah.
 * Kalau endpoint belum ada, kembalikan array kosong — peta tetap tampil.
 */
export async function getActiveTools(): Promise<string[]> {
  try {
    const res = await fetch(`${API_BASE}/api/v1/agent/tools`, { headers: headers() });
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data)
      ? data.map((t: { name: string } | string) => (typeof t === "string" ? t : t.name))
      : [];
  } catch {
    return [];
  }
}

export async function getProviders() {
  const res = await fetch(`${API_BASE}/api/v1/agent/providers`, { headers: headers() });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json() as Promise<{ name: Provider; available: boolean }[]>;
}

// ─── Chat (SSE) ───────────────────────────────────────────────────────────────

export async function streamChat(params: {
  messages: { role: Role; content: string }[];
  conversationId?: string | null;
  provider?: Provider;
  taskType?: TaskType;
  agentMode?: boolean;
  pillar?: string;
  focusTool?: string;
  onDelta: (delta: string, provider: string) => void;
  onStep?: (step: AgentStep) => void;
  onDone: (info: { usage?: Usage; conversationId?: string }) => void;
  onError: (message: string) => void;
  signal?: AbortSignal;
}) {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/v1/agent/chat`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        messages: params.messages,
        conversation_id: params.conversationId ?? null,
        provider: params.provider ?? null,
        task_type: params.taskType ?? "general",
        agent_mode: params.agentMode ?? true,
        pillar: params.pillar ?? null,
        focus_tool: params.focusTool ?? null,
      }),
      signal: params.signal,
    });
  } catch (e) {
    params.onError(e instanceof Error ? e.message : "Gagal menghubungi server");
    return;
  }

  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    params.onError(e.detail ?? `HTTP ${res.status}`);
    return;
  }

  const reader = res.body?.getReader();
  if (!reader) { params.onError("Tidak ada isi respons"); return; }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const ev = JSON.parse(line.slice(6));
          if (ev.type === "delta" && ev.delta) params.onDelta(ev.delta, ev.provider ?? "");
          else if (ev.type === "step" && ev.step) params.onStep?.(ev.step);
          else if (ev.type === "done") params.onDone({ usage: ev.usage, conversationId: ev.conversation_id });
          else if (ev.type === "error") params.onError(ev.message ?? "Terjadi kesalahan");
        } catch {
          /* lewati SSE yang rusak */
        }
      }
    }
  } catch (e) {
    // Pembatalan oleh pengguna bukan kegagalan — jangan tampilkan sebagai galat.
    // AbortController melempar dari reader.read(), dan tanpa penanganan ini
    // Next.js menampilkannya sebagai runtime error di layar.
    const aborted =
      (e instanceof DOMException && e.name === "AbortError") ||
      params.signal?.aborted;
    if (!aborted) {
      params.onError(e instanceof Error ? e.message : "Koneksi terputus");
    }
  }
}

// ─── Conversations ────────────────────────────────────────────────────────────

export async function listConversations(): Promise<Conversation[]> {
  const res = await fetch(`${API_BASE}/api/v1/conversations`, { headers: headers() });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  const res = await fetch(`${API_BASE}/api/v1/conversations/${id}`, { headers: headers() });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

export async function deleteConversation(id: string) {
  await fetch(`${API_BASE}/api/v1/conversations/${id}`, { method: "DELETE", headers: headers() });
}

// ─── Knowledge base ───────────────────────────────────────────────────────────

export async function uploadDocuments(files: File[], namespace?: string) {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  if (namespace) form.append("namespace", namespace);

  const res = await fetch(`${API_BASE}/api/v1/documents/upload`, {
    method: "POST",
    headers: headers(false),   // biarkan browser menyusun boundary multipart
    body: form,
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.detail ?? `Upload gagal: ${res.status}`);
  }
  return res.json() as Promise<{
    results: UploadResult[];
    total_uploaded: number;
    total_failed: number;
  }>;
}

export interface ClipResult {
  document_id: string | null;
  title: string;
  status: string;
  chunk_count: number;
  error: string | null;
}

/** Simpan satu halaman web ke knowledge base. */
export async function clipUrl(url: string, opts?: { namespace?: string; renderJs?: boolean }) {
  const res = await fetch(`${API_BASE}/api/v1/documents/clip`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      url,
      namespace: opts?.namespace ?? null,
      render_js: opts?.renderJs ?? false,
    }),
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.detail ?? `Gagal menyimpan halaman: ${res.status}`);
  }
  return res.json() as Promise<ClipResult>;
}

export interface ObsidianResult {
  status: string;
  error: string | null;
  vault?: string;
  added: number;
  updated: number;
  skipped: number;
  failed: number;
  removed: number;
  errors?: string[];
}

/** Apakah vault Obsidian sudah diatur di .env dan terbaca? */
export async function obsidianStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/v1/documents/obsidian/status`, { headers: headers() });
    if (!res.ok) return { configured: false, vault: null };
    return res.json() as Promise<{ configured: boolean; vault: string | null }>;
  } catch {
    return { configured: false, vault: null };
  }
}

/** Baca vault Obsidian ke knowledge base (inkremental). */
export async function syncObsidian(prune = false): Promise<ObsidianResult> {
  const res = await fetch(`${API_BASE}/api/v1/documents/obsidian/sync`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ prune }),
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.detail ?? `Sinkronisasi gagal: ${res.status}`);
  }
  return res.json();
}

export async function listDocuments(): Promise<DocumentItem[]> {
  const res = await fetch(`${API_BASE}/api/v1/documents`, { headers: headers() });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

export async function deleteDocument(id: string) {
  await fetch(`${API_BASE}/api/v1/documents/${id}`, { method: "DELETE", headers: headers() });
}

export async function knowledgeStats() {
  const res = await fetch(`${API_BASE}/api/v1/documents/stats`, { headers: headers() });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json() as Promise<{
    total_documents: number;
    total_chunks: number;
    by_status: Record<string, number>;
  }>;
}