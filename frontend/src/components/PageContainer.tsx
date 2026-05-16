import { cn } from "@/lib/utils";

/**
 * Standard page wrapper.
 *
 * Renders an optional full-bleed header band (with bottom separator) followed
 * by a scrollable, width-constrained body. Pages should pass their PageHeader
 * via the `header` prop so every screen shares the same top bar.
 */
export function PageContainer({
  children,
  header,
  className,
  maxWidth = "max-w-6xl",
}: {
  children: React.ReactNode;
  header?: React.ReactNode;
  className?: string;
  maxWidth?: string;
}) {
  return (
    <div className="flex h-full flex-col">
      {header && (
        <div className="shrink-0 border-b border-border bg-card/30 px-8 py-4">
          {header}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className={cn("mx-auto px-8 py-6", maxWidth, className)}>{children}</div>
      </div>
    </div>
  );
}
