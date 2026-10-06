import type { Ticket } from "../../../../contracts/api/types";
import { useWorkspace } from "../workspace";

export function dependencyWaitLabel(dependency: NonNullable<Ticket["dependency_waits"]>[number]): string {
  if (dependency.state === "needs_revalidation") return `Perlu validasi ulang tiket #${dependency.number}`;
  if (dependency.phase === "cancelled") return `Tiket dependency #${dependency.number} dibatalkan`;
  if (dependency.phase === "integrating") return `Menunggu integrasi tiket #${dependency.number}`;
  if (dependency.phase === "accepted") return `Menunggu validasi dependency tiket #${dependency.number}`;
  return `Menunggu tiket #${dependency.number} diterima`;
}

export default function DependencyNotice({ ticket }: { ticket: Ticket }) {
  const { selectTicket } = useWorkspace();
  if (!ticket.dependency_waits?.length) return null;
  return (
    <div className="notice dependency-notice" role="note" aria-label="Menunggu dependency">
      {ticket.dependency_waits.map((dependency) => (
        <div key={dependency.upstream_id}>
          <span>{dependencyWaitLabel(dependency)}. </span>
          <button type="button" className="link" title={dependency.title}
            onClick={() => selectTicket(dependency.upstream_id)}>Lihat tiket #{dependency.number}</button>
        </div>
      ))}
    </div>
  );
}
