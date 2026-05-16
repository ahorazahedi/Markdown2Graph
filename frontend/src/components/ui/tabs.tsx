import * as React from "react";
import * as T from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

export const Tabs = T.Root;

export const TabsList = React.forwardRef<
  React.ElementRef<typeof T.List>,
  React.ComponentPropsWithoutRef<typeof T.List>
>(({ className, ...props }, ref) => (
  <T.List
    ref={ref}
    className={cn("inline-flex items-center gap-1 border-b border-border", className)}
    {...props}
  />
));
TabsList.displayName = "TabsList";

export const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof T.Trigger>,
  React.ComponentPropsWithoutRef<typeof T.Trigger>
>(({ className, ...props }, ref) => (
  <T.Trigger
    ref={ref}
    className={cn(
      "relative h-8 px-3 text-sm text-muted-foreground hover:text-foreground transition-colors",
      "data-[state=active]:text-foreground",
      "data-[state=active]:after:absolute data-[state=active]:after:left-0 data-[state=active]:after:right-0 data-[state=active]:after:-bottom-px data-[state=active]:after:h-px data-[state=active]:after:bg-foreground",
      className,
    )}
    {...props}
  />
));
TabsTrigger.displayName = "TabsTrigger";

export const TabsContent = T.Content;
