import { cn } from "@/lib/utils";

/**
 * Standard scrollable page wrapper. Use for any page that doesn't manage
 * its own full-bleed layout. The vertical scroll lives here, so the
 * sidebar stays pinned.
 */
export function PageContainer({
  children,
  className,
  maxWidth = "max-w-6xl",
}: {
  children: React.ReactNode;
  className?: string;
  maxWidth?: string;
}) {
  return (
    <div className="h-full overflow-y-auto">
      <div className={cn("mx-auto px-8 py-8", maxWidth, className)}>{children}</div>
    </div>
  );
}
