import type { Json, Phase, Run, Ticket } from "../../../contracts/api/types";

export const PHASES: { phase: Phase; label: string; hint: string }[] = [
  { phase: "draft", label: "Draf", hint: "Belum diajukan untuk review" },
  { phase: "scope_review", label: "Review scope", hint: "Menunggu persetujuan scope Anda" },
  { phase: "ready", label: "Siap", hint: "Scope disetujui, menunggu pekerjaan" },
  { phase: "development", label: "Development", hint: "Developer bekerja" },
  { phase: "technical_review", label: "Review teknis", hint: "Technical lead memeriksa kandidat" },
  { phase: "qa", label: "QA", hint: "Verifikasi oleh harness" },
  { phase: "uat", label: "UAT", hint: "Menunggu keputusan Anda" },
  { phase: "integrating", label: "Integrasi", hint: "Antrean integrasi" },
  { phase: "accepted", label: "Diterima", hint: "Sudah terintegrasi" },
];

export const RUN_STATUS_LABEL: Record<Run["status"], string> = {
  queued: "Antre", running: "Berjalan", waiting_input: "Menunggu jawaban", waiting_quota: "Menunggu kuota",
  succeeded: "Selesai", failed: "Gagal", cancelled: "Dibatalkan", stopped: "Dihentikan",
};
export const ACTIVE_RUN = new Set<Run["status"]>(["queued", "running", "waiting_input", "waiting_quota"]);

export const ROLE_LABEL: Record<string, string> = {
  po: "Product Owner", "technical-lead": "Technical Lead", developer: "Developer", qa: "QA",
};

export function senderLabel(sender: string): string {
  if (sender.startsWith("user")) return "Anda";
  if (sender.startsWith("agent:")) return ROLE_LABEL[sender.slice(6)] ?? sender.slice(6);
  return sender;
}

/** Board order: larger priority first, then ticket number. The GUI is the only writer of this convention. */
export function byPriority(a: Ticket, b: Ticket): number {
  return b.priority - a.priority || a.number - b.number;
}

export function short(value: string | null | undefined, length = 10): string {
  return value ? value.slice(0, length) : "—";
}

export function text(value: Json | undefined): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" });
}

export function blockerLabel(blocker: Json): string | null {
  if (!blocker || typeof blocker !== "object" || Array.isArray(blocker)) return null;
  const reason = blocker.reason;
  const labels: Record<string, string> = {
    needs_human: "Perlu keputusan Anda: batas perbaikan tercapai",
    dependency_revalidation: "Dependency perlu divalidasi ulang",
    integration_blocked: "Integrasi diblokir: Git/DB perlu diperiksa operator",
  };
  return typeof reason === "string" ? labels[reason] ?? reason : "Terblokir";
}

/** Draft of an unsent message survives a reload but never leaves this browser. */
export function loadDraft(key: string): string {
  try { return window.sessionStorage.getItem(key) ?? ""; } catch { return ""; }
}
export function saveDraft(key: string, value: string): void {
  try { value ? window.sessionStorage.setItem(key, value) : window.sessionStorage.removeItem(key); } catch { /* storage may be unavailable */ }
}
