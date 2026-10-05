import type { ApiErrorBody, Board, Commands, Project, Message, Run, TicketDetail, Artifact,
  Candidate, StateEvent, SnapshotRequired } from "../../../../contracts/api/types";

export class ApiError extends Error {
  constructor(readonly status: number, readonly detail: ApiErrorBody["error"]) { super(detail.message); }
}

export class ApiClient {
  private csrf = "";
  readonly base: string;
  constructor(base = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000") {
    const url = new URL(base);
    if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.username || url.password
      || url.pathname !== "/" || url.search || url.hash) throw new Error("Expected a local control API origin");
    this.base = url.origin;
  }
  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(this.base + path, { ...init, credentials: "include" });
    if (!response.ok) {
      // A proxy, crash page or truncated body is not our JSON envelope: still surface a typed error.
      let body: ApiErrorBody | undefined;
      try { body = await response.json() as ApiErrorBody; } catch { body = undefined; }
      throw new ApiError(response.status, body?.error ?? {
        code: "http_error", message: response.statusText || `Request failed (${response.status})`, details: {} });
    }
    return response.json() as Promise<T>;
  }
  async login(code: string): Promise<void> {
    const result = await this.request<{ csrf_token: string }>("/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }),
    });
    this.csrf = result.csrf_token;
  }
  async session(): Promise<void> {
    const result = await this.request<{ csrf_token: string }>("/auth/session");
    this.csrf = result.csrf_token;
  }
  async logout(): Promise<void> {
    await this.request("/auth/logout", { method: "POST", headers: { "X-CSRF-Token": this.csrf } });
    this.csrf = "";
  }
  /** Keep the same key for retries of one logical action. No automatic retry of commands. */
  command<K extends keyof Commands>(path: string, body: Commands[K]["body"], key: string): Promise<Commands[K]["response"]> {
    if (!key || !this.csrf) throw new Error("Session and a stable command key are required");
    return this.request(path, { method: "POST", headers: { "Content-Type": "application/json",
      "X-CSRF-Token": this.csrf, "Idempotency-Key": key }, body: JSON.stringify(body) });
  }
  projects() { return this.request<{ projects: Project[] }>("/projects"); }
  board(projectId: string) { return this.request<Board>(`/projects/${encodeURIComponent(projectId)}/tickets`); }
  ticket(ticketId: string) { return this.request<TicketDetail>(`/tickets/${encodeURIComponent(ticketId)}`); }
  messages(projectId: string) { return this.request<{ messages: Message[]; cursor: number }>(`/projects/${encodeURIComponent(projectId)}/messages`); }
  run(runId: string) { return this.request<{ run: Run; input: Message | null }>(`/runs/${encodeURIComponent(runId)}`); }
  logs(runId: string, generation: number, afterSeq = 0) {
    return this.request<{ messages: Message[] }>(`/runs/${encodeURIComponent(runId)}/logs?generation=${generation}&after_seq=${afterSeq}`);
  }
  candidate(ticketId: string, candidateId: string) {
    return this.request<{ candidate: Candidate }>(`/tickets/${encodeURIComponent(ticketId)}/candidates/${encodeURIComponent(candidateId)}`);
  }
  artifact(artifactId: string) { return this.request<{ artifact: Artifact }>(`/artifacts/${encodeURIComponent(artifactId)}`); }
  artifactUrl(artifactId: string) { return `${this.base}/artifacts/${encodeURIComponent(artifactId)}/content`; }
  watch(projectId: string, cursor: number, handlers: {
    open?: () => void;
    event: (event: StateEvent) => void;
    snapshot: (snapshot: Board, reason: SnapshotRequired["reason"]) => void | Promise<void>;
    error: (error: unknown) => void;
  }): () => void {
    let source: EventSource | undefined;
    let closed = false;
    const open = () => {
      source = new EventSource(`${this.base}/projects/${encodeURIComponent(projectId)}/events?cursor=${cursor}`, { withCredentials: true });
      source.onopen = () => { if (!closed) handlers.open?.(); };
      source.addEventListener("state", (raw: MessageEvent) => {
        try {
          const event = JSON.parse(raw.data) as StateEvent;
          if (event.cursor <= cursor) return;
          handlers.event(event); // Only acknowledge after the state update succeeds.
          cursor = event.cursor;
        } catch (error) { source?.close(); handlers.error(error); }
      });
      source.addEventListener("snapshot_required", async (raw: MessageEvent) => {
        source?.close();
        try {
          const reset = JSON.parse(raw.data) as SnapshotRequired;
          const snapshot = await this.board(projectId);
          if (closed) return;
          await handlers.snapshot(snapshot, reset.reason);
          if (closed) return;
          cursor = snapshot.cursor;
          open();
        } catch (error) { handlers.error(error); }
      });
      source.addEventListener("session_expired", () => {
        source?.close(); handlers.error(new ApiError(401, { code: "session_expired", message: "Authenticate again", details: {} }));
      });
      // Native reconnect sends Last-Event-ID, which the API prioritises over the original URL.
      // Handler failures above close the stream so they cannot silently advance its cursor.
      source.onerror = () => {
        if (!closed) handlers.error(new Error("Event stream disconnected; reconnect using the last snapshot/cursor"));
      };
    };
    open();
    return () => { closed = true; source?.close(); };
  }
}
