import * as T from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

export const TooltipProvider = T.Provider;

export function Tip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <T.Root delayDuration={300}>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content sideOffset={4} className="z-50 max-w-sm rounded bg-fg px-2 py-1 text-xs text-bg shadow">
          {content}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
