import * as T from "@radix-ui/react-tabs";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export const Tabs = T.Root;
export const TabsContent = T.Content;

export function TabsList({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <T.List className={cn("inline-flex rounded-md bg-muted p-0.5 ring-1 ring-border", className)}>
      {children}
    </T.List>
  );
}

export function TabsTrigger({ value, children }: { value: string; children: ReactNode }) {
  return (
    <T.Trigger
      value={value}
      className="rounded px-3 py-1 text-sm text-muted-fg data-[state=active]:bg-card data-[state=active]:text-fg data-[state=active]:shadow cursor-pointer"
    >
      {children}
    </T.Trigger>
  );
}
