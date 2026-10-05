import { Component, useState, type ReactNode } from "react";
import { ApiError } from "../api/client";

export function FakeBadge({ title = "Keluaran fake provider/runtime: bukan QA nyata" }: { title?: string }) {
  return <span className="badge badge--fake" title={title}>FAKE · bukan QA nyata</span>;
}

export function Badge({ tone = "neutral", children, title }: {
  tone?: "neutral" | "info" | "warn" | "bad" | "good"; children: ReactNode; title?: string;
}) {
  return <span className={`badge badge--${tone}`} title={title}>{children}</span>;
}

export function describeError(error: unknown): { title: string; detail: string } {
  if (error instanceof ApiError) {
    const d = error.detail.details ?? {};
    if (error.detail.code === "revision_conflict") {
      const numbers = d.expected !== undefined ? ` (diharapkan ${String(d.expected)}, sekarang ${String(d.actual)})` : "";
      return {
        title: "Data sudah berubah (conflict)",
        detail: `Perubahan Anda tidak diterapkan karena revisi berubah${numbers}. Data terbaru sudah dimuat; periksa lalu ulangi.`,
      };
    }
    return { title: `Permintaan ditolak (${error.status} ${error.detail.code})`, detail: error.detail.message };
  }
  return { title: "Tidak dapat menghubungi backend", detail: error instanceof Error ? error.message : String(error) };
}

export function ErrorBanner({ error, onDismiss }: { error: unknown; onDismiss: () => void }) {
  if (!error) return null;
  const { title, detail } = describeError(error);
  return (
    <div className="error-banner" role="alert">
      <div><strong>{title}</strong><p>{detail}</p></div>
      <button type="button" className="ghost" onClick={onDismiss} aria-label="Tutup pesan error">Tutup</button>
    </div>
  );
}

/** Two-step explicit action: the first click only reveals the confirmation. */
type ConfirmationProps = {
  label: string; confirmLabel: string; onConfirm: () => void | Promise<void>; disabled?: boolean;
  tone?: "primary" | "danger"; children?: ReactNode; intent?: string;
};
export function ConfirmButton(props: ConfirmationProps) {
  return <Confirmation key={props.intent ?? props.label} {...props} />;
}
function Confirmation({ label, confirmLabel, onConfirm, disabled, tone = "primary", children }: ConfirmationProps) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <button type="button" className={tone} disabled={disabled} onClick={() => setAsking(true)}>{label}</button>;
  return (
    <span className="confirm" role="group" aria-label={`Konfirmasi: ${label}`}>
      {children}
      <button type="button" className={tone} disabled={disabled} onClick={async () => { setAsking(false); await onConfirm(); }}>{confirmLabel}</button>
      <button type="button" className="ghost" onClick={() => setAsking(false)}>Batal</button>
    </span>
  );
}

/** A rendering bug must show a message, never a blank page that hides the user's state. */
export class CrashGuard extends Component<{ children: ReactNode }, { failure: Error | null }> {
  state = { failure: null as Error | null };
  static getDerivedStateFromError(failure: Error) { return { failure }; }
  render() {
    if (!this.state.failure) return this.props.children;
    return (
      <div className="error-banner" role="alert">
        <div><strong>Tampilan gagal dirender</strong><p>{this.state.failure.message}. Data di backend tidak berubah; muat ulang halaman.</p></div>
        <button type="button" onClick={() => window.location.reload()}>Muat ulang</button>
      </div>
    );
  }
}
