import { useAppState } from "@/state/AppState";
import { fmtDate } from "@/lib/formatting";

export function ParticipantSelector({ compact }: { compact?: boolean }) {
  const { participants, selectedId, setSelectedId, selected } = useAppState();
  const disabled = participants.state !== "success" || participants.data.length === 0;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="participant" className="text-[12px] font-medium uppercase tracking-wide text-muted-foreground">
        Participant
      </label>
      <select
        id="participant"
        value={selectedId ?? ""}
        disabled={disabled}
        onChange={(e) => setSelectedId(e.target.value)}
        className="h-10 min-w-[240px] rounded-lg border bg-card px-3 text-[14px] text-foreground transition-colors duration-150 hover:border-primary disabled:cursor-not-allowed disabled:opacity-60"
      >
        {participants.state === "loading" && <option>Loading participants…</option>}
        {participants.state === "error" && <option>Participants unavailable</option>}
        {participants.state === "success" && participants.data.length === 0 && <option>No participants</option>}
        {participants.state === "success" &&
          participants.data.map((p) => (
            <option key={p.participant_id} value={p.participant_id}>
              {p.label ? `${p.label} · ${p.participant_id}` : p.participant_id}
            </option>
          ))}
      </select>
      {!compact && selected?.observation_start && (
        <span className="text-xs text-muted-foreground">
          Observation period {fmtDate(selected.observation_start)} – {fmtDate(selected.observation_end)}
        </span>
      )}
    </div>
  );
}
