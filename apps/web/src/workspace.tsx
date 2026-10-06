import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Board, Commands, Message, TicketDetail } from "../../../contracts/api/types";
import { ApiError } from "./api/client";
import { api } from "./api/instance";

export type Connection = "loading" | "live" | "reconnecting";

interface Workspace {
  projectId: string;
  board: Board | null;
  messages: Message[];
  detail: TicketDetail | null;
  connection: Connection;
  artifactEpoch: number;
  error: unknown;
  busy: boolean;
  selectedTicket: string | null;
  selectTicket: (id: string | null) => void;
  dismissError: () => void;
  report: (error: unknown) => void;
  refresh: () => Promise<void>;
  /** Idempotent command. Resolves undefined (after surfacing the error) when it fails. */
  command: <K extends keyof Commands>(path: string, body: Commands[K]["body"]) => Promise<Commands[K]["response"] | undefined>;
}

const Context = createContext<Workspace | null>(null);

export function useWorkspace(): Workspace {
  const value = useContext(Context);
  if (!value) throw new Error("useWorkspace outside WorkspaceProvider");
  return value;
}

export function WorkspaceProvider({ projectId, selectedTicket, selectTicket, onAuthLost, children }: {
  projectId: string; selectedTicket: string | null; selectTicket: (id: string | null) => void;
  onAuthLost: () => void; children: ReactNode;
}) {
  const [board, setBoard] = useState<Board | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [detail, setDetail] = useState<TicketDetail | null>(null);
  const [connection, setConnection] = useState<Connection>("loading");
  const [artifactEpoch, setArtifactEpoch] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(0);
  const selected = useRef(selectedTicket);
  selected.current = selectedTicket;
  const sequence = useRef({ issued: 0, applied: 0 });
  const keys = useRef(new Map<string, string>());
  const authLost = useRef(onAuthLost);
  authLost.current = onAuthLost;

  const report = useCallback((failure: unknown) => {
    if (failure instanceof ApiError && failure.status === 401) { authLost.current(); return; }
    setError(failure);
  }, []);

  const refresh = useCallback(async () => {
    const ticketId = selected.current;
    const order = sequence.current;
    const mine = ++order.issued;
    try {
      const [nextBoard, nextMessages, nextDetail] = await Promise.all([
        api.board(projectId), api.messages(projectId), ticketId ? api.ticket(ticketId) : Promise.resolve(null),
      ]);
      // A slower, older response must never overwrite a newer one.
      if (order !== sequence.current || mine < order.applied) return;
      order.applied = mine;
      setBoard(nextBoard);
      setMessages(nextMessages.messages);
      if (ticketId === selected.current) setDetail(nextDetail);
    } catch (failure) {
      if (order !== sequence.current || mine < order.applied) return;
      if (failure instanceof ApiError && failure.status === 404 && ticketId === selected.current) setDetail(null);
      report(failure);
    }
  }, [projectId, report]);

  useEffect(() => {
    setBoard(null); setMessages([]); setDetail(null); setConnection("loading"); setError(null);
    sequence.current = { issued: 0, applied: 0 };
  }, [projectId]);
  useEffect(() => { void refresh(); }, [refresh, selectedTicket]);

  useEffect(() => {
    let alive = true;
    let stop = () => {};
    let timer: number | undefined;
    const schedule = () => {
      if (timer !== undefined) return;
      timer = window.setTimeout(() => { timer = undefined; if (alive) void refresh(); }, 80);
    };
    void (async () => {
      let cursor = 0;
      try {
        cursor = (await api.board(projectId)).cursor;
        // Read every displayed resource AFTER this cursor. Changes during those reads are replayed.
        await refresh();
      } catch (failure) { if (alive) report(failure); return; }
      if (!alive) return;
      stop = api.watch(projectId, cursor, {
        open: () => { if (alive) { setConnection("live"); setArtifactEpoch((n) => n + 1); schedule(); } },
        event: (event) => {
          setConnection("live");
          if (event.type.startsWith("artifact.")) setArtifactEpoch((n) => n + 1);
          schedule();
        },
        snapshot: async () => { if (alive) { setConnection("live"); setArtifactEpoch((n) => n + 1); await refresh(); } },
        error: (failure) => {
          if (failure instanceof ApiError) report(failure);
          else if (alive) setConnection("reconnecting");
        },
      });
    })();
    return () => { alive = false; window.clearTimeout(timer); stop(); };
  }, [projectId, refresh, report]);

  const command = useCallback(async <K extends keyof Commands>(path: string, body: Commands[K]["body"]) => {
    // The same logical request keeps one key until the server answers, so a network retry never duplicates it.
    const signature = `${path}\n${JSON.stringify(body)}`;
    let key = keys.current.get(signature);
    if (!key) { key = crypto.randomUUID(); keys.current.set(signature, key); }
    setPending((n) => n + 1);
    try {
      const result = await api.command<K>(path, body, key);
      keys.current.delete(signature);
      void refresh();
      return result;
    } catch (failure) {
      if (failure instanceof ApiError && failure.status < 500) keys.current.delete(signature);
      report(failure);
      if (failure instanceof ApiError && failure.status === 409) void refresh(); // show current state next to the conflict
      return undefined;
    } finally { setPending((n) => n - 1); }
  }, [refresh, report]);

  const value = useMemo<Workspace>(() => ({
    projectId, board, messages, detail, connection, artifactEpoch, error, busy: pending > 0, selectedTicket, selectTicket,
    dismissError: () => setError(null), report, refresh, command,
  }), [projectId, board, messages, detail, connection, artifactEpoch, error, pending, selectedTicket, selectTicket, report, refresh, command]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
