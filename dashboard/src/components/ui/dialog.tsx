import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export const Dialog = D.Root;
export const DialogTrigger = D.Trigger;
export const DialogClose = D.Close;

export function DialogContent({
  title,
  description,
  children,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-40 bg-black/60" />
      <D.Content
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-[min(560px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg bg-card p-5 shadow-xl ring-1 ring-border max-h-[90vh] overflow-y-auto",
          className,
        )}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <D.Title className="text-base font-semibold">{title}</D.Title>
            {description ? (
              <D.Description className="mt-1 text-sm text-muted-fg">{description}</D.Description>
            ) : (
              <D.Description className="sr-only">{title}</D.Description>
            )}
          </div>
          <D.Close className="rounded p-1 text-muted-fg hover:bg-muted" aria-label="Close">
            <X size={16} />
          </D.Close>
        </div>
        {children}
      </D.Content>
    </D.Portal>
  );
}

/** A panel sliding from the right (event details and the like). */
export function Sheet({
  open,
  onOpenChange,
  title,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  children: ReactNode;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <D.Content className="fixed right-0 top-0 z-50 flex h-full w-[min(640px,100vw)] flex-col bg-card shadow-2xl ring-1 ring-border">
          <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-3">
            <D.Title className="truncate text-sm font-semibold">{title}</D.Title>
            <D.Description className="sr-only">Details</D.Description>
            <D.Close className="rounded p-1 text-muted-fg hover:bg-muted" aria-label="Close">
              <X size={16} />
            </D.Close>
          </div>
          <div className="flex-1 overflow-y-auto p-5">{children}</div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
