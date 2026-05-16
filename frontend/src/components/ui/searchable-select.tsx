import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Check, RefreshCw, Search } from "lucide-react";
import { cn } from "@/lib/utils";

export interface SearchableOption {
  value: string;
  label: string;
  hint?: string;
}

interface Props {
  value: string;
  onChange: (v: string) => void;
  options: SearchableOption[];
  placeholder?: string;
  loading?: boolean;
  onRefresh?: () => void;
  emptyText?: string;
  disabled?: boolean;
  allowCustom?: boolean;
}

export function SearchableSelect({
  value,
  onChange,
  options,
  placeholder = "Select…",
  loading,
  onRefresh,
  emptyText = "No matches.",
  disabled,
  allowCustom = true,
}: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  useEffect(() => {
    if (open) {
      setQ("");
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return options;
    return options.filter(
      (o) => o.value.toLowerCase().includes(needle) || o.label.toLowerCase().includes(needle),
    );
  }, [q, options]);

  const displayed = value || "";
  const matches = options.find((o) => o.value === value);

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex h-8 w-full items-center justify-between rounded-sm border border-border bg-background px-2.5 text-left text-sm",
          "disabled:cursor-not-allowed disabled:opacity-50",
          !displayed && "text-muted-foreground",
        )}
      >
        <span className="truncate">{matches?.label || displayed || placeholder}</span>
        <ChevronDown className="ml-2 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      </button>

      {open && (
        <div className="absolute left-0 right-0 top-full z-50 mt-1 overflow-hidden rounded-sm border border-border bg-card shadow-lg">
          <div className="flex items-center gap-1.5 border-b border-border px-2 py-1.5">
            <Search className="h-3.5 w-3.5 text-muted-foreground" />
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search models…"
              className="h-6 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              onKeyDown={(e) => {
                if (e.key === "Escape") setOpen(false);
                if (e.key === "Enter" && allowCustom && q.trim() && filtered.length === 0) {
                  onChange(q.trim());
                  setOpen(false);
                }
              }}
            />
            {onRefresh && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onRefresh();
                }}
                disabled={loading}
                title="Refresh models"
                className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
              </button>
            )}
          </div>
          <div className="max-h-72 overflow-y-auto py-1">
            {loading && options.length === 0 ? (
              <div className="px-3 py-2 text-xs text-muted-foreground">Loading…</div>
            ) : filtered.length === 0 ? (
              <div className="px-3 py-2 text-xs text-muted-foreground">
                {emptyText}
                {allowCustom && q.trim() && (
                  <button
                    className="ml-2 underline hover:text-foreground"
                    onClick={() => {
                      onChange(q.trim());
                      setOpen(false);
                    }}
                  >
                    Use "{q.trim()}"
                  </button>
                )}
              </div>
            ) : (
              filtered.map((o) => {
                const active = o.value === value;
                return (
                  <button
                    key={o.value}
                    type="button"
                    onClick={() => {
                      onChange(o.value);
                      setOpen(false);
                    }}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left text-sm",
                      "hover:bg-accent hover:text-foreground",
                      active && "bg-accent/60",
                    )}
                  >
                    <span className="flex-1 truncate font-mono text-xs">{o.label}</span>
                    {o.hint && <span className="text-2xs text-muted-foreground">{o.hint}</span>}
                    {active && <Check className="h-3.5 w-3.5 text-foreground" />}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
