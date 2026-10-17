import { Check, Eraser } from "lucide-react";
import { useAck, useClear } from "@/api/queries";
import type { Fault } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useCan } from "@/lib/auth";
import { ConfirmDialog } from "./ConfirmDialog";

export function FaultActions({ fault }: { fault: Fault }) {
  const can = useCan();
  const ack = useAck();
  const clear = useClear();
  if (!can("operator")) return null;
  const latched = fault.state === "LATCHED";
  return (
    <div className="flex items-center gap-1.5">
      {!fault.acknowledged ? (
        <ConfirmDialog
          trigger={
            <Button size="sm" aria-label={`Acknowledge ${fault.code}`}>
              <Check size={12} /> Ack
            </Button>
          }
          title={`Acknowledge ${fault.code}`}
          description="Tells the others someone is on it. The fault stays open."
          confirm="Acknowledge"
          onConfirm={(note) => ack.mutateAsync({ id: fault.fault_id, note })}
        />
      ) : null}
      <ConfirmDialog
        trigger={
          <Button size="sm" variant={latched ? "danger" : "secondary"} aria-label={`Clear ${fault.code}`}>
            <Eraser size={12} /> Clear
          </Button>
        }
        title={`Clear ${fault.code}`}
        description={
          latched
            ? "A latched fault stays until a human clears it: make sure the cause is gone. Its symptoms clear with it."
            : "Forces the fault closed. If the cause is still there it comes back."
        }
        confirm="Clear"
        danger={latched}
        requireNote={latched}
        noteLabel="Reason"
        onConfirm={(reason) => clear.mutateAsync({ id: fault.fault_id, reason })}
      />
    </div>
  );
}
