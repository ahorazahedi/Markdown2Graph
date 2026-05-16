import * as React from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  width = "max-w-3xl",
}: {
  open: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  width?: string;
}) {
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/40 backdrop-blur-[1px]" onClick={onClose} />
      <aside className={cn("flex h-full w-full flex-col border-l border-border bg-background", width)}>
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-border px-5 py-3">
          <div className="min-w-0">
            {title && <div className="truncate text-sm font-semibold tracking-tightish">{title}</div>}
            {subtitle && <div className="truncate text-xs text-muted-foreground">{subtitle}</div>}
          </div>
          <button
            onClick={onClose}
            className="rounded-sm p-1 text-muted-foreground hover:bg-accent"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="flex-1 overflow-auto">{children}</div>
        {footer && <footer className="shrink-0 border-t border-border px-5 py-3">{footer}</footer>}
      </aside>
    </div>
  );
}
