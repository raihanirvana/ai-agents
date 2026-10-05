/** DEV-008 public DTOs. JSON timestamps are ISO UTC strings; IDs are opaque strings. */
import type * as Body from "./requests";
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Role = "po" | "technical-lead" | "developer" | "qa";
export type Phase = Body.Phase;
export type Revision = Body.Revision;
export type Empty = Body.Empty;
export type Criterion = Body.Criterion;
export type Scope = Body.Scope;
export type ProjectCreate = Body.ProjectCreate;
export interface Project {
  id: string; name: string; mode: "new" | "existing"; brief: string; brief_version: number;
  revision: number; onboarding: string; accepted_tip: string | null;
  onboarding_detail?: { job_id: string; report_artifact_id?: string; source_sha?: string | null;
    baseline_sha?: string | null; dirty?: boolean; dirty_status?: string[]; dirty_total?: number; patch_applied?: boolean;
    blocker?: string | null; required_checks?: string } | null;
}
export interface Ticket {
  id: string; project_id: string; number: number; title: string; phase: Phase; revision: number;
  scope_version: number; priority: number; blocker: Json; repair_cycles: number; repair_limit: number;
}
export interface Usage { [name: string]: number | string[] }
export interface Run {
  id: string; project_id: string; ticket_id: string | null; scope_version: number | null;
  revision: number; generation: number; role: Role; runtime: string; fake: boolean;
  lane: "interactive" | "execution"; stage: string;
  status: "queued" | "running" | "waiting_input" | "waiting_quota" | "succeeded" | "failed" | "cancelled" | "stopped";
  attempt: number; usage: Usage; attempt_usage: Usage; limits: { [key: string]: number | string };
  result: { [key: string]: Json } | null; request_id: string | null; context_artifact_id: string | null;
  available_at: string | null;
}
export interface InputRequest {
  id: string; thread_id: string; project_id: string; ticket_id: string | null; scope_version: number | null;
  recipient: string; job_id: string | null; generation: number | null;
  status: "open" | "answered" | "stale_scope" | "cancelled" | "orphaned" | "escalated";
  escalated_request_id?: string; answer_id: string | null; answer: string | null; attempt_status: string | null;
}
export interface Message {
  id: string; project_id: string; ticket_id: string | null; thread_id: string; seq: number;
  sender: string; recipient: string | null; kind: string; body: string; reply_to: string | null;
  metadata: { [key: string]: Json }; attachment_ids: string[]; created_at: string; input?: InputRequest;
}
export interface Artifact {
  id: string; project_id: string; kind: string; storage: "file" | "git"; checksum: string;
  size_bytes: number | null; availability: "available" | "unavailable"; unavailable_reason: string | null;
  metadata: { [key: string]: Json };
}
export interface Verification {
  id: string; status: string; target_digest: string; evidence_ids: string[];
  counts: { [key: string]: number }; results: { [key: string]: Json }; uac_coverage: { [key: string]: string[] };
}
export type PreviewStatus = "requested" | "starting" | "ready" | "stopping" | "stopped" | "failed";
export interface Preview {
  id: string; project_id: string; ticket_id: string; candidate_id: string; scope_version: number;
  target_artifact_id: string; target_digest: string; status: PreviewStatus; revision: number;
  /** Set only while ready. The preview origin is localhost, never the 127.0.0.1 control host. */
  url: string | null; port: number | null; stop_reason: string | null; error: string | null;
  details: { [key: string]: Json }; requested_at: string; ready_at: string | null; stopped_at: string | null;
}
export interface Integration {
  operation_id: string; status: "pending" | "done" | "diverged" | "blocked"; expected_base: string; target_sha: string;
  observed_tip: string | null; reason: string | null; evidence_artifact_id: string | null;
}
export interface Candidate {
  id: string; ticket_id: string; scope_version: number; commit_sha: string; base_sha: string; status: string;
  target_artifact_id: string | null; target_digest: string | null; evidence_ids: string[]; preview: Json;
  commit_artifact_id: string; build_artifact_id: string | null; live_preview: Preview | null;
  integrated_sha: string | null; integration: Integration | null;
  verifications: Verification[];
}
export interface Board { project: Project; tickets: Ticket[]; runs: Run[]; preview: Preview | null; cursor: number }
export interface TicketDetail {
  ticket: Ticket; versions: { version: number; title: string; description: string; uac: Criterion[]; scope: Json }[];
  dependencies: { upstream_id: string; state: string; scope_version: number | null; candidate_id: string | null;
    integration_sha: string | null; revalidation: Json }[];
  approvals: { id: string; type: string; scope_version: number | null; candidate_id: string | null;
    target_digest: string | null; evidence_ids: string[]; details: Json }[];
  candidates: Candidate[]; messages: Message[]; cursor: number;
}
export interface ApiErrorBody { error: { code: string; message: string; details: { [key: string]: Json } } }
export interface StateEvent {
  cursor: number; project_id: string; type: string; entity_type: string | null; entity_id: string | null;
  run_id: string | null; payload: { [key: string]: Json }; created_at: string;
}
export interface SnapshotRequired { reason: "cursor_expired" | "cursor_ahead"; snapshot_url: string; cursor: number }
export type MessageCreate = Body.MessageCreate;
export type InputAnswer = Body.InputAnswer;
export type UatDecision = Body.Uat;
export interface Commands {
  onboarding: { body: Body.Onboarding; response: { project: Project; job_id: string } };
  createProject: { body: ProjectCreate; response: { project: Project } };
  brief: { body: Revision & { brief: string }; response: { project: Project } };
  createTicket: { body: Scope; response: { ticket: Ticket } };
  editScope: { body: Revision & { document: Scope }; response: { ticket: Ticket } };
  approveScope: { body: { items: (Revision & { ticket_id: string; scope_version: number })[] }; response: { batch_id: string; tickets: Ticket[] } };
  proposalDecision: { body: Revision & { accept: boolean }; response: { ticket: Ticket } };
  decision: { body: { accept: boolean }; response: { message_id: string } };
  priority: { body: Revision & { priority: number }; response: { ticket: Ticket } };
  cancel: { body: Revision; response: { ticket: Ticket } };
  changes: { body: Revision & { candidate_id: string; reason: string }; response: { ticket: Ticket } };
  repair: { body: Revision & { additional_cycles: number }; response: { ticket: Ticket } };
  uat: { body: UatDecision; response: { integration: { operation_id: string; [key: string]: Json } } };
  waiver: { body: Revision & { ticket_id: string; fingerprint_artifact_id: string; reason: string }; response: { approval_id: string } };
  release: { body: Revision & { target_artifact_id: string; target_digest: string; evidence_ids: string[] }; response: { release_id: string; revision: number; status: string } };
  message: { body: MessageCreate; response: { message: Message; job_id: string | null } };
  input: { body: InputAnswer; response: { answer_id: string; resumed: boolean } };
  nonblockingInput: { body: { scope_version: number | null; generation: number; answer: string }; response: { answer_id: string; resumed: boolean } };
  stop: { body: Empty; response: { run: Run; cleanup: "supervisor_pending" } };
  startPreview: { body: Empty; response: { preview: Preview } };
  stopPreview: { body: Empty; response: { preview: Preview } };
  budget: { body: Revision & { additions: { [key: string]: number } }; response: { run: Run } };
}
