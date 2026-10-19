import { cva, type VariantProps } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

const button = cva(
  "inline-flex items-center justify-center gap-1.5 rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 disabled:pointer-events-none disabled:opacity-40 cursor-pointer",
  {
    variants: {
      variant: {
        default: "bg-sky-600 text-white hover:bg-sky-500",
        secondary: "bg-muted text-fg hover:bg-muted/70 ring-1 ring-border",
        ghost: "hover:bg-muted text-fg",
        danger: "bg-red-600 text-white hover:bg-red-500",
        outline: "ring-1 ring-border hover:bg-muted text-fg",
      },
      size: { sm: "h-7 px-2.5 text-xs", md: "h-8 px-3", icon: "h-8 w-8" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

/** A button's look, for a link that acts as one. */
export const buttonStyle = button;

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof button>;

export function Button({ className, variant, size, type = "button", ...props }: ButtonProps) {
  return <button type={type} className={cn(button({ variant, size }), className)} {...props} />;
}
