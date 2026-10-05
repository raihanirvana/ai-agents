import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Component, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import type { Group } from "three";
import type { Role } from "../../../../../contracts/api/types";
import { ROLE_LABEL, senderLabel, when } from "../../format";
import { useWorkspace } from "../../workspace";
import { projectOffice, type OfficeAgent, type OfficeState } from "./projection";

const STATE_LABEL: Record<OfficeState, string> = {
  idle: "Siaga", planning: "Menyusun rencana", coding: "Mengembangkan", reviewing: "Meninjau",
  testing: "Menguji", waiting_for_user: "Menunggu input", blocked: "Terhambat",
};

const COLORS: Record<OfficeState, string> = {
  idle: "#79918b", planning: "#7785c2", coding: "#cf8d43", reviewing: "#7866b2",
  testing: "#238775", waiting_for_user: "#c48a27", blocked: "#ba5848",
};

function motionPreference(): boolean {
  try {
    const saved = window.localStorage.getItem("office-animation");
    if (saved !== null) return saved === "on";
    return !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch { return true; }
}

export default function Office() {
  const { board, messages, selectTicket, connection } = useWorkspace();
  const [selected, setSelected] = useState<Role>("po");
  const [animate, setAnimate] = useState(motionPreference);
  if (!board) return <p className="muted pad" role="status">Memuat kantor…</p>;
  const agents = projectOffice(board, messages);
  const current = agents.find((agent) => agent.role === selected) ?? agents[0];
  const threadMessages = current.latestMessage
    ? messages.filter((message) => message.thread_id === current.latestMessage?.thread_id)
      .sort((a, b) => a.seq - b.seq).slice(-6)
    : [];
  const setAnimation = () => {
    const next = !animate;
    setAnimate(next);
    try { window.localStorage.setItem("office-animation", next ? "on" : "off"); } catch { /* storage can be disabled */ }
  };
  const openContext = (agent: OfficeAgent) => {
    setSelected(agent.role);
    if (agent.ticket) selectTicket(agent.ticket.id);
    else if (agent.latestMessage?.ticket_id) selectTicket(agent.latestMessage.ticket_id);
  };

  return (
    <section className="office" aria-label="Kantor tim">
      <div className="office-head">
        <div><p className="eyebrow">RUANG KERJA</p><h2>Kantor tim</h2>
          <p className="muted">Status dan percakapan berasal dari data proyek; kantor tidak menjalankan pekerjaan.</p></div>
        <button type="button" className="small" aria-pressed={animate} onClick={setAnimation}>
          Animasi {animate ? "aktif" : "nonaktif"}
        </button>
      </div>
      <div className="office-connection" role="status">{connection === "live" ? "Status mengikuti event langsung" : "Status dari snapshot; menyambung ulang…"}</div>
      {board.runs.some((run) => run.fake) && <p className="office-fake" role="note">Ada run FAKE di proyek ini; tampilannya bukan bukti kerja model nyata.</p>}
      <div className="office-scene" aria-label="Scene kantor tiga dimensi">
        <WebGLBoundary fallback={<OfficeFallback agents={agents} selected={selected} onSelect={openContext} />}>
          <Canvas
            aria-label="Kantor 3D empat peran"
            camera={{ position: [0, 6.3, 10], fov: 42 }}
            dpr={[1, 1.5]}
            frameloop={animate && agents.some((agent) => agent.state !== "idle") ? "always" : "demand"}
            gl={{ antialias: false, powerPreference: "low-power" }}
            onCreated={({ gl }) => {
              const canvas = gl.domElement;
              canvas.setAttribute("role", "img");
              canvas.setAttribute("aria-label", "Kantor 3D. Gunakan daftar peran di bawah untuk navigasi.");
            }}
          >
            <Suspense fallback={null}>
              <Scene agents={agents} selected={selected} onSelect={openContext} animate={animate} />
            </Suspense>
          </Canvas>
        </WebGLBoundary>
      </div>
      <div className="office-roles" aria-label="Peran tim">
        {agents.map((agent) => (
          <button key={agent.role} type="button" className={selected === agent.role ? "office-role office-role--selected" : "office-role"}
            aria-pressed={selected === agent.role} onClick={() => openContext(agent)}>
            <span className="office-role__dot" style={{ backgroundColor: COLORS[agent.state] }} aria-hidden="true" />
            <span><strong>{ROLE_LABEL[agent.role]}</strong><small>{STATE_LABEL[agent.state]}</small></span>
          </button>
        ))}
      </div>
      <section className="office-context" aria-live="polite" aria-label={`Konteks ${ROLE_LABEL[current.role]}`}>
        <div className="office-context__head"><h3>{ROLE_LABEL[current.role]}</h3><span>{STATE_LABEL[current.state]}</span></div>
        {current.run && <p className="muted">Run {current.run.id.slice(0, 8)} · {current.run.stage} · {current.run.status}</p>}
        {current.ticket && <p><button type="button" className="link" onClick={() => selectTicket(current.ticket!.id)}>#{current.ticket.number} {current.ticket.title}</button></p>}
        {!current.run && !current.latestMessage && <p className="muted">Belum ada aktivitas peran ini.</p>}
        {threadMessages.length > 0 && <ol className="office-thread">
          {threadMessages.map((message) => <li key={message.id} data-message-id={message.id}>
            <strong>{senderLabel(message.sender)}</strong><time dateTime={message.created_at}>{when(message.created_at)}</time>
            <p>{message.body}</p>
            {message.ticket_id && <button type="button" className="link" onClick={() => selectTicket(message.ticket_id)}>Buka tiket terkait</button>}
          </li>)}
        </ol>}
      </section>
    </section>
  );
}

function Scene({ agents, selected, onSelect, animate }: {
  agents: OfficeAgent[]; selected: Role; onSelect: (agent: OfficeAgent) => void; animate: boolean;
}) {
  return <>
    <color attach="background" args={["#edf4f1"]} />
    <ambientLight intensity={1.4} />
    <directionalLight position={[4, 8, 6]} intensity={2.2} />
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.03, 0]} receiveShadow>
      <planeGeometry args={[12, 8]} /><meshStandardMaterial color="#dce9e3" />
    </mesh>
    <OfficeFurniture />
    {agents.map((agent, index) => <AgentStation key={agent.role} agent={agent} index={index}
      selected={selected === agent.role} onSelect={() => onSelect(agent)} animate={animate} />)}
  </>;
}

function OfficeFurniture() {
  return <>
    <mesh position={[0, 0.45, -2]} castShadow><boxGeometry args={[9, 0.12, 0.1]} /><meshStandardMaterial color="#78958a" /></mesh>
    <mesh position={[-4.42, 1.5, -2]}><boxGeometry args={[0.08, 2.1, 0.08]} /><meshStandardMaterial color="#78958a" /></mesh>
    <mesh position={[4.42, 1.5, -2]}><boxGeometry args={[0.08, 2.1, 0.08]} /><meshStandardMaterial color="#78958a" /></mesh>
    <mesh position={[0, 1.5, -2.02]}><boxGeometry args={[3.2, 1.2, 0.1]} /><meshStandardMaterial color="#d8e7df" /></mesh>
    <mesh position={[0, 0.12, 0]}><boxGeometry args={[1.15, 0.14, 4.8]} /><meshStandardMaterial color="#c3d5cc" /></mesh>
  </>;
}

function AgentStation({ agent, index, selected, onSelect, animate }: {
  agent: OfficeAgent; index: number; selected: boolean; onSelect: () => void; animate: boolean;
}) {
  const x = (index - 1.5) * 2.3;
  const color = COLORS[agent.state];
  return <group position={[x, 0, 0]}>
    <mesh position={[0, 0.65, -0.7]} castShadow><boxGeometry args={[1.5, 0.12, 0.85]} /><meshStandardMaterial color="#8b6954" /></mesh>
    <mesh position={[0, 0.99, -0.85]}><boxGeometry args={[0.72, 0.48, 0.06]} /><meshStandardMaterial color="#233c3c" /></mesh>
    <mesh position={[0, 1, -0.81]}><planeGeometry args={[0.62, 0.37]} /><meshBasicMaterial color={color} /></mesh>
    <mesh position={[-0.44, 0.89, -0.25]}><cylinderGeometry args={[0.12, 0.12, 0.44, 12]} /><meshStandardMaterial color="#4b705d" /></mesh>
    <group position={[0, 0, 0.45]} onClick={(event) => { event.stopPropagation(); onSelect(); }}>
      <AgentFigure color={color} animate={animate && agent.state !== "idle"} />
      <mesh position={[0, 1.25, 0]}><sphereGeometry args={[0.6, 12, 8]} /><meshBasicMaterial color={color} transparent opacity={selected ? 0.2 : 0.1} /></mesh>
    </group>
  </group>;
}

function AgentFigure({ color, animate }: { color: string; animate: boolean }) {
  return <group>
    <AnimatedHead animate={animate}>
      <mesh position={[0, 1.38, 0]} castShadow><sphereGeometry args={[0.22, 16, 12]} /><meshStandardMaterial color="#d9a77f" /></mesh>
    </AnimatedHead>
    <mesh position={[0, 0.94, 0]} castShadow><capsuleGeometry args={[0.25, 0.45, 4, 8]} /><meshStandardMaterial color={color} /></mesh>
    <mesh position={[0, 0.57, 0]} castShadow><boxGeometry args={[0.46, 0.12, 0.25]} /><meshStandardMaterial color="#36464a" /></mesh>
  </group>;
}

function AnimatedHead({ animate, children }: { animate: boolean; children: ReactNode }) {
  const head = useRef<Group | null>(null);
  const invalidate = useThree((state) => state.invalidate);
  // On-demand rendering stops the frame loop, so settle the head and draw once when motion ends.
  useEffect(() => {
    if (!animate && head.current) { head.current.position.y = 0; invalidate(); }
  }, [animate, invalidate]);
  useFrame(({ clock }) => {
    if (head.current) head.current.position.y = animate ? Math.sin(clock.elapsedTime * 2.4) * 0.035 : 0;
  });
  return <group ref={head}>{children}</group>;
}

class WebGLBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

function OfficeFallback({ agents, selected, onSelect }: {
  agents: OfficeAgent[]; selected: Role; onSelect: (agent: OfficeAgent) => void;
}) {
  return <div className="office-fallback" role="status"><strong>Scene 3D tidak tersedia</strong>
    <span>Kantor tetap dapat dipakai dari daftar peran dan board.</span>
    <div>{agents.map((agent) => <button key={agent.role} type="button" aria-pressed={selected === agent.role}
      onClick={() => onSelect(agent)}>{ROLE_LABEL[agent.role]} · {STATE_LABEL[agent.state]}</button>)}</div>
  </div>;
}
