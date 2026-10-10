import type { Ticket } from "../../../../contracts/api/types";
import { useWorkspace } from "../workspace";

export default function RebaseNotice({ ticket, compact = false }: { ticket: Ticket; compact?: boolean }) {
  const { selectTicket } = useWorkspace();
  if (ticket.phase !== "development" || !ticket.rebase_notice) return null;
  const source = ticket.rebase_notice.source_ticket;
  return (
    <div className="notice dependency-notice" role="note" aria-label="Penyesuaian kode terbaru">
      <strong>Menyesuaikan kode terbaru.</strong>{" "}
      {source ? <>Kode utama berubah setelah tiket #{source.number} diterima. </>
        : <>Kode utama proyek berubah. </>}
      {!compact && <>
        Developer menggabungkan pekerjaan tiket ini dengan kode terbaru. Pekerjaan sebelumnya tetap tersimpan.
        Setelah penyesuaian, tiket melewati review teknis, QA, dan UAT kembali.
        Penyesuaian ini tidak menambah jumlah siklus perbaikan.{" "}
      </>}
      {source && <button type="button" className="link" title={source.title}
        onClick={() => selectTicket(source.id)}>Lihat tiket #{source.number}</button>}
    </div>
  );
}
