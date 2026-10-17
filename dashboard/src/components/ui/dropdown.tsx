import * as M from "@radix-ui/react-dropdown-menu";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export const Menu = M.Root;
export const MenuTrigger = M.Trigger;

export function MenuContent({ children, align = "end" }: { children: ReactNode; align?: "start" | "end" }) {
  return (
    <M.Portal>
      <M.Content
        align={align}
        sideOffset={6}
        className="z-50 min-w-[12rem] rounded-md bg-card p-1 shadow-xl ring-1 ring-border"
      >
        {children}
      </M.Content>
    </M.Portal>
  );
}

export function MenuItem({
  children,
  onSelect,
  disabled,
  className,
}: {
  children: ReactNode;
  onSelect?: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <M.Item
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        "flex cursor-pointer select-none items-center gap-2 rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-muted data-[disabled]:opacity-40",
        className,
      )}
    >
      {children}
    </M.Item>
  );
}

export const MenuLabel = ({ children }: { children: ReactNode }) => (
  <M.Label className="px-2 py-1 text-[11px] uppercase tracking-wider text-muted-fg">{children}</M.Label>
);
export const MenuSeparator = () => <M.Separator className="my-1 h-px bg-border" />;
