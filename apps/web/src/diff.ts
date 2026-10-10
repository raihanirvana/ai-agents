import type { Criterion, Json } from "../../../contracts/api/types";

export interface ScopeDoc { title: string; description?: string; uac: Criterion[]; dependencies?: string[]; reverts_candidate_id?: string; qa_profile?: "lightweight" | "manual" }
export type Change = { kind: "same" | "added" | "removed" | "changed"; label: string; before?: string; after?: string };

const mode = (c: Criterion) => c.mode ?? "automated";

export function asScope(value: Json | undefined, fallback: ScopeDoc): ScopeDoc {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fallback;
  const doc = value as { [key: string]: Json };
  return {
    title: typeof doc.title === "string" ? doc.title : fallback.title,
    description: typeof doc.description === "string" ? doc.description : fallback.description ?? "",
    uac: Array.isArray(doc.uac) ? (doc.uac as unknown as Criterion[]) : fallback.uac,
    ...(doc.qa_profile === "lightweight" || doc.qa_profile === "manual" ? { qa_profile: doc.qa_profile } : {}),
    dependencies: Array.isArray(doc.dependencies) ? (doc.dependencies as string[]) : [],
    ...(typeof doc.reverts_candidate_id === "string" ? { reverts_candidate_id: doc.reverts_candidate_id } : {}),
  };
}

/** Field-level diff between the approved/current scope and a PO proposal. */
export function diffScope(before: ScopeDoc, after: ScopeDoc): Change[] {
  const changes: Change[] = [];
  const field = (label: string, a: string, b: string) =>
    changes.push(a === b ? { kind: "same", label, after: b } : { kind: "changed", label, before: a, after: b });
  field("Judul", before.title, after.title);
  field("Deskripsi", before.description ?? "", after.description ?? "");
  const old = new Map(before.uac.map((c) => [c.id, c]));
  const next = new Map(after.uac.map((c) => [c.id, c]));
  for (const [id, c] of next) {
    const prev = old.get(id);
    if (!prev) changes.push({ kind: "added", label: id, after: `${c.text} (${mode(c)})` });
    else if (prev.text !== c.text || mode(prev) !== mode(c))
      changes.push({ kind: "changed", label: id, before: `${prev.text} (${mode(prev)})`, after: `${c.text} (${mode(c)})` });
    else changes.push({ kind: "same", label: id, after: `${c.text} (${mode(c)})` });
  }
  for (const [id, c] of old) if (!next.has(id)) changes.push({ kind: "removed", label: id, before: `${c.text} (${mode(c)})` });
  field("Dependency", (before.dependencies ?? []).join(", "), (after.dependencies ?? []).join(", "));
  field("Kandidat yang direvert", before.reverts_candidate_id ?? "", after.reverts_candidate_id ?? "");
  return changes;
}
