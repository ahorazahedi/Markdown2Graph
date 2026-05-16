import * as React from "react";
import * as P from "@radix-ui/react-progress";
import { cn } from "@/lib/utils";

export const Progress = React.forwardRef<
  React.ElementRef<typeof P.Root>,
  React.ComponentPropsWithoutRef<typeof P.Root> & { value?: number }
>(({ className, value = 0, ...props }, ref) => (
  <P.Root
    ref={ref}
    className={cn("relative h-2 w-full overflow-hidden rounded-full bg-secondary", className)}
    {...props}
  >
    <P.Indicator
      className="h-full w-full flex-1 bg-primary transition-all"
      style={{ transform: `translateX(-${100 - Math.min(100, Math.max(0, value))}%)` }}
    />
  </P.Root>
));
Progress.displayName = "Progress";
