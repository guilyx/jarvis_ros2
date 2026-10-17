import { useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input, Label } from "@/components/ui/input";
import { ErrorNote } from "./domain";

/**
 * A command behind a confirmation. Dangerous ones (clear a latched fault, disable recovery,
 * approve a plan) need a note: it goes into the audit trail with the caller's name.
 */
export function ConfirmDialog({
  trigger,
  title,
  description,
  confirm,
  danger = false,
  requireNote = false,
  noteLabel = "Note",
  onConfirm,
}: {
  trigger: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  confirm: string;
  danger?: boolean;
  requireNote?: boolean;
  noteLabel?: string;
  onConfirm: (note: string) => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm(note.trim());
      setOpen(false);
      setNote("");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setError(null);
      }}
    >
      <span onClick={() => setOpen(true)} className="contents">
        {trigger}
      </span>
      <DialogContent title={title} description={description}>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <label className="block">
            <Label
              hint={
                requireNote ? "(required, recorded with your name)" : "(optional, recorded with your name)"
              }
            >
              {noteLabel}
            </Label>
            <Input
              autoFocus
              className="w-full"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              aria-label={noteLabel}
            />
          </label>
          <ErrorNote error={error} />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant={danger ? "danger" : "default"}
              disabled={busy || (requireNote && !note.trim())}
            >
              {confirm}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
