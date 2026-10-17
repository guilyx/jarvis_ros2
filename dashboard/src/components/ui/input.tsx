import type { InputHTMLAttributes, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

const field =
  "h-8 rounded-md bg-bg px-2.5 text-sm ring-1 ring-border placeholder:text-muted-fg focus:outline-none focus:ring-2 focus:ring-sky-500";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(field, className)} {...props} />;
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(field, "pr-7", className)} {...props} />;
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(field, "h-auto min-h-20 py-2 font-mono text-xs", className)} {...props} />;
}

export function Label({ children, hint }: { children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <span className="mb-1 block text-xs font-medium text-muted-fg">
      {children}
      {hint ? <span className="ml-1 font-normal opacity-70">{hint}</span> : null}
    </span>
  );
}
