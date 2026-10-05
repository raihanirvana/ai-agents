import type { Board, Message, Role, Run, Ticket } from "../../../../../contracts/api/types";

export type OfficeState = "idle" | "planning" | "coding" | "reviewing" | "testing" | "waiting_for_user" | "blocked";
export interface OfficeAgent {
  role: Role;
  state: OfficeState;
  run: Run | null;
  ticket: Ticket | null;
  latestMessage: Message | null;
}

const ROLE_ORDER: Role[] = ["po", "technical-lead", "developer", "qa"];
const ACTIVE = new Set<Run["status"]>(["queued", "running", "waiting_input", "waiting_quota"]);

function stateFor(run: Run | null): OfficeState {
  if (!run) return "idle";
  if (run.status === "waiting_input" || run.status === "waiting_quota") return "waiting_for_user";
  if (run.status === "failed") return "blocked";
  if (run.status === "succeeded" || run.status === "cancelled" || run.status === "stopped") return "idle";
  if (run.role === "developer") return "coding";
  if (run.role === "qa") return "testing";
  if (run.role === "technical-lead") return run.stage.includes("review") ? "reviewing" : "planning";
  return "planning";
}

/** Project the same persisted board/message snapshot used by Activity and Chat. */
export function projectOffice(board: Board, messages: Message[]): OfficeAgent[] {
  return ROLE_ORDER.map((role) => {
    // Board runs are ordered by creation time; choose the newest active attempt,
    // otherwise the newest run so an old failure cannot keep an idle role blocked.
    const roleRuns = board.runs.filter((item) => item.role === role);
    const run = [...roleRuns].reverse().find((item) => ACTIVE.has(item.status))
      ?? roleRuns[roleRuns.length - 1] ?? null;
    const latestMessage = [...messages]
      .filter((message) => message.sender === `agent:${role}` || message.recipient === `role:${role}`)
      .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.seq - a.seq)[0] ?? null;
    return {
      role,
      state: stateFor(run),
      run,
      ticket: run?.ticket_id ? board.tickets.find((ticket) => ticket.id === run.ticket_id) ?? null : null,
      latestMessage,
    };
  });
}
