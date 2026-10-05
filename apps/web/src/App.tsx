import { useCallback, useEffect, useState } from "react";
import { api } from "./api/instance";
import Login from "./components/Login";
import Projects from "./components/Projects";
import Workspace from "./components/Workspace";
import { WorkspaceProvider } from "./workspace";

type Auth = "checking" | "anonymous" | "authenticated";
interface Route { project: string | null; ticket: string | null }

/** Selection lives in the URL hash so a reload (or a shared tab) restores the same context. */
function parse(hash: string): Route {
  const match = /^#\/p\/([^/]+)(?:\/t\/([^/]+))?$/.exec(hash);
  return match ? { project: decodeURIComponent(match[1]), ticket: match[2] ? decodeURIComponent(match[2]) : null } : { project: null, ticket: null };
}
function href(route: Route): string {
  if (!route.project) return "#/";
  const ticket = route.ticket ? "/t/" + encodeURIComponent(route.ticket) : "";
  return "#/p/" + encodeURIComponent(route.project) + ticket;
}

export default function App() {
  const [auth, setAuth] = useState<Auth>("checking");
  const [expired, setExpired] = useState(false);
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));

  useEffect(() => {
    api.session().then(() => setAuth("authenticated"), () => setAuth("anonymous"));
  }, []);
  useEffect(() => {
    const onHash = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = useCallback((next: Route) => { window.location.hash = href(next); }, []);
  const authLost = useCallback(() => { setExpired(true); setAuth("anonymous"); }, []);
  const project = route.project;
  const selectTicket = useCallback((ticket: string | null) => go({ project, ticket }), [go, project]);

  if (auth === "checking") return <main className="page-shell"><p className="muted" role="status" aria-busy="true">Memeriksa sesi…</p></main>;
  if (auth === "anonymous") return <Login expired={expired} onLogin={() => { setExpired(false); setAuth("authenticated"); }} />;
  if (!route.project) return <Projects onAuthLost={authLost} onOpen={(project) => go({ project, ticket: null })} />;
  return (
    <WorkspaceProvider key={route.project} projectId={route.project} selectedTicket={route.ticket} selectTicket={selectTicket} onAuthLost={authLost}>
      <Workspace onProjects={() => go({ project: null, ticket: null })}
        onLogout={async () => { try { await api.logout(); } finally { setExpired(false); setAuth("anonymous"); go({ project: null, ticket: null }); } }} />
    </WorkspaceProvider>
  );
}
