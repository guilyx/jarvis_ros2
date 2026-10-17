import { useDecide } from "@/api/queries";
import type { Execution } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";

/** Reject / Approve for an execution waiting for an operator. Approving needs a note (audited). */
export function DecideButtons({ x, description }: { x: Execution; description?: string }) {
  const decide = useDecide();
  return (
    <div className="flex gap-1.5">
      <ConfirmDialog
        trigger={<Button size="sm">Reject</Button>}
        title={`Reject ${x.plan_id}`}
        confirm="Reject"
        onConfirm={(note) => decide.mutateAsync({ id: x.execution_id, approve: false, note })}
      />
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
    </div>
  );
}
