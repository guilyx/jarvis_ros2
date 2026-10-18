import { useDecide, useMe } from "@/api/queries";
import type { Execution } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";

const same = (a = "", b = "") => a.trim().toLowerCase() === b.trim().toLowerCase();

/**
 * Reject / Approve for an execution waiting for an operator. Approving needs a note (audited).
 * Four-eyes: whoever asked for a run may withdraw it (reject) but not approve it; the recovery
 * component refuses that too, here the button just isn't offered.
 */
export function DecideButtons({ x, description }: { x: Execution; description?: string }) {
  const decide = useDecide();
  const me = useMe().data;
  const mine = x.requester.type === "OPERATOR" && !!me && same(x.requester.id, me.name);
  return (
    <div className="flex items-center gap-1.5">
      <ConfirmDialog
        trigger={<Button size="sm">{mine ? "Withdraw" : "Reject"}</Button>}
        title={`${mine ? "Withdraw" : "Reject"} ${x.plan_id}`}
        confirm={mine ? "Withdraw" : "Reject"}
        onConfirm={(note) => decide.mutateAsync({ id: x.execution_id, approve: false, note })}
      />
      {mine ? (
        <span
          className="text-xs text-muted-fg"
          title="You asked for this run: an operator may not approve their own"
        >
          Another operator approves
        </span>
      ) : (
        <ConfirmDialog
          trigger={
            <Button size="sm" variant="default">
              Approve
            </Button>
          }
          title={`Approve ${x.plan_id}`}
          description={description}
          confirm="Approve"
          requireNote
          onConfirm={(note) => decide.mutateAsync({ id: x.execution_id, approve: true, note })}
        />
      )}
    </div>
  );
}
