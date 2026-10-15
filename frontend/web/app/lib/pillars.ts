/**
 * Peta wilayah Prometheus.
 *
 * Tiap bintang = satu tool nyata di backend. `tool` HARUS sama persis dengan
 * nama yang di-register di ToolRegistry, karena dipakai untuk mencocokkan
 * langkah agent (SSE step) ke bintang yang benar.
 *
 * `on` diisi saat runtime dari daftar tool aktif backend — tool hanya
 * ter-register kalau credential-nya ada di .env (lihat dependencies.py).
 */

export type Capability = {
  id: string;
  x: number;
  y: number;
  label: string;
  tool: string;
  ask: string;
  mag: 0 | 1 | 2;
  icon: string;
  on?: boolean;
};

export type Pillar = {
  id: string;
  name: string;
  sub: string;
  blurb: string;
  prompt: string;
  hue: string;
  icon: string;
  taskType: "general" | "coding" | "research" | "quick";
  hub: { x: number; y: number };
  label: { x: number; y: number };
  labelAnchor: "top" | "bottom";
  mini: { x: number; y: number }[];
  miniEdges: [number, number][];
  trace: string[];
  stars: Capability[];
};

export const C = {
  void: "#0B0E18",
  deep: "#12162A",
  cream: "#F2EDE3",
  line: "#2C3654",
  text: "#E4E9F7",
  dim: "#5A6484",
  faint: "#232B45",
  flare: "#7FC5E8",
  violet: "#9B87E0",
  gold: "#E0B888",
} as const;

export const CAMERA = "cubic-bezier(.33,.02,.18,1)";
export const SURFACE = "cubic-bezier(.2,.7,.3,1)";
export const CHROME = "cubic-bezier(.4,0,.2,1)";
export const ENTER_MS = 780;

export const CORE = { x: 500, y: 300 };
export const MAG_R = [5, 4, 3.3];
export const MAG_O = [1, 0.82, 0.66];

export const PILLARS: Pillar[] = [
  {
    id: "personal",
    name: "Personal OS",
    sub: "jadwal · acara · waktu luang",
    blurb: "Atur hari lo tanpa buka kalender.",
    prompt: "Jadwal gua hari ini apa aja?",
    hue: C.flare,
    icon: "Calendar",
    taskType: "general",
    hub: { x: 500, y: 210 },
    label: { x: 500, y: 92 },
    labelAnchor: "bottom",
    mini: [{ x: 444, y: 156 }, { x: 512, y: 124 }, { x: 588, y: 146 }, { x: 400, y: 186 }],
    miniEdges: [[-1, 0], [0, 1], [1, 2], [-1, 3]],
    trace: ["p1", "p2", "p3", "p4", "p5"],
    stars: [
      { id: "p1", x: 252, y: 292, label: "Lihat jadwal", tool: "calendar_list_events", mag: 1, icon: "Calendar",
        ask: "Jadwal gua hari ini apa aja?" },
      { id: "p2", x: 500, y: 196, label: "Buat acara", tool: "calendar_create_event", mag: 0, icon: "CalendarPlus",
        ask: "Bikin acara review PR besok jam 2 siang." },
      { id: "p3", x: 748, y: 300, label: "Cari waktu kosong", tool: "calendar_check_free_busy", mag: 1, icon: "Clock3",
        ask: "Kapan gua ada slot 2 jam minggu ini?" },
      { id: "p4", x: 646, y: 424, label: "Hapus acara", tool: "calendar_delete_event", mag: 2, icon: "CalendarX",
        ask: "Hapus meeting standup hari Jumat." },
      { id: "p5", x: 338, y: 420, label: "Tugas", tool: "tasks", mag: 2, icon: "ListTodo",
        ask: "Tugas apa yang jatuh tempo minggu ini?" },
    ],
  },
  {
    id: "dev",
    name: "Dev Copilot",
    sub: "kode · PR · issue",
    blurb: "Review dan telusuri repo tanpa pindah tab.",
    prompt: "Ada PR yang perlu gua review?",
    hue: C.violet,
    icon: "GitPullRequest",
    taskType: "coding",
    hub: { x: 640, y: 358 },
    label: { x: 806, y: 486 },
    labelAnchor: "top",
    mini: [{ x: 712, y: 384 }, { x: 780, y: 354 }, { x: 836, y: 396 }, { x: 688, y: 438 }, { x: 766, y: 300 }],
    miniEdges: [[-1, 0], [0, 1], [1, 2], [-1, 3], [1, 4]],
    trace: ["d1", "d2", "d3", "d6", "d4", "d5"],
    stars: [
      { id: "d1", x: 204, y: 300, label: "Daftar PR", tool: "github_list_prs", mag: 1, icon: "GitPullRequest",
        ask: "Ada PR yang perlu gua review?" },
      { id: "d2", x: 436, y: 198, label: "Baca PR", tool: "github_get_pr", mag: 0, icon: "FileDiff",
        ask: "Ringkas perubahan di PR #12." },
      { id: "d3", x: 688, y: 224, label: "Daftar issue", tool: "github_list_issues", mag: 1, icon: "CircleDot",
        ask: "Issue open yang label bug apa aja?" },
      { id: "d6", x: 838, y: 340, label: "Baca file", tool: "file_read", mag: 2, icon: "FileText",
        ask: "Baca isi file app/memory/manager.py." },
      { id: "d4", x: 612, y: 412, label: "Info repo", tool: "github_get_repo", mag: 1, icon: "FolderGit2",
        ask: "Commit terakhir di repo prometheus apa?" },
      { id: "d5", x: 336, y: 420, label: "Cari kode", tool: "github_search_code", mag: 2, icon: "Code",
        ask: "Cari fungsi hybrid search di repo." },
    ],
  },
  {
    id: "brain",
    name: "Second Brain",
    sub: "dokumen · ingatan · web",
    blurb: "Semua yang pernah lo simpan, bisa dipanggil lagi.",
    prompt: "Cari di dokumen gua soal pgvector.",
    hue: C.gold,
    icon: "Brain",
    taskType: "research",
    hub: { x: 360, y: 358 },
    label: { x: 194, y: 486 },
    labelAnchor: "top",
    mini: [{ x: 288, y: 384 }, { x: 220, y: 354 }, { x: 164, y: 396 }, { x: 312, y: 438 }, { x: 234, y: 300 }],
    miniEdges: [[-1, 0], [0, 1], [1, 2], [-1, 3], [1, 4]],
    trace: ["b1", "b7", "b2", "b3", "b6", "b4", "b5"],
    stars: [
      { id: "b1", x: 218, y: 320, label: "Cari dokumen", tool: "rag_search", mag: 0, icon: "FileSearch",
        ask: "Cari di dokumen gua soal pgvector." },
      { id: "b7", x: 322, y: 232, label: "Isi perpustakaan", tool: "knowledge_list", mag: 1, icon: "Library",
        ask: "Dokumen apa aja yang udah gua simpan?" },
      { id: "b2", x: 432, y: 204, label: "Ingat", tool: "memory_read", mag: 1, icon: "Brain",
        ask: "Gua pernah cerita apa soal setup Ollama?" },
      { id: "b3", x: 666, y: 222, label: "Simpan ingatan", tool: "memory_write", mag: 1, icon: "BookmarkPlus",
        ask: "Simpan: gua pakai llama3.1:8b di RTX 3050." },
      { id: "b6", x: 844, y: 330, label: "Jelajah folder", tool: "file_list", mag: 2, icon: "FolderOpen",
        ask: "Lihat isi folder backend/app." },
      { id: "b4", x: 624, y: 418, label: "Buka halaman", tool: "browser_fetch", mag: 2, icon: "Globe",
        ask: "Buka dan ringkas halaman dokumentasi pgvector." },
      { id: "b5", x: 352, y: 424, label: "Cari di web", tool: "browser_search", mag: 1, icon: "Search",
        ask: "Cari kabar terbaru soal LangGraph." },
    ],
  },
];

/** Cari wilayah yang memiliki tool tertentu — dipakai memetakan step agent. */
export function pillarOfTool(tool: string): Pillar | undefined {
  return PILLARS.find((p) => p.stars.some((s) => s.tool === tool));
}

/** Label manusiawi dari nama tool teknis. */
export function labelOfTool(tool: string): string {
  for (const p of PILLARS) {
    const s = p.stars.find((x) => x.tool === tool);
    if (s) return s.label;
  }
  return tool;
}

/**
 * Tandai bintang mana yang hidup berdasarkan tool yang benar-benar
 * ter-register di backend. Tool tanpa credential tidak akan muncul di daftar,
 * jadi bintangnya tampil redup — jujur menggambarkan kondisi sistem.
 */
export function applyAvailability(active: string[]): Pillar[] {
  const set = new Set(active);
  return PILLARS.map((p) => ({
    ...p,
    stars: p.stars.map((s) => ({ ...s, on: set.has(s.tool) })),
  }));
}