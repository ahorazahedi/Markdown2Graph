import * as React from "react";
import { cn } from "@/lib/utils";

type Variant = "default" | "secondary" | "outline" | "destructive" | "success" | "warning";

export function Badge({
  className,
  variant = "default",
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { variant?: Variant }) {
  const styles: Record<Variant, string> = {
    default: "bg-secondary text-secondary-foreground",
    secondary: "bg-muted text-muted-foreground",
    outline: "border border-border text-foreground",
    destructive: "bg-destructive/15 text-destructive border border-destructive/30",
    success: "bg-[hsl(var(--success))]/15 text-[hsl(var(--success))] border border-[hsl(var(--success))]/30",
    warning: "bg-[hsl(var(--warning))]/15 text-[hsl(var(--warning))] border border-[hsl(var(--warning))]/30",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm px-1.5 py-0.5 text-2xs font-medium tracking-tight",
        styles[variant],
        className,
      )}
      {...props}
    />
  );
}
