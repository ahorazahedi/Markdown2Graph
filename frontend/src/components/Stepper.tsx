import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

export interface Step {
  key: string;
  title: string;
  description?: string;
}

export function Stepper({
  steps,
  current,
  orientation = "vertical",
}: {
  steps: Step[];
  current: number;
  orientation?: "vertical" | "horizontal";
}) {
  if (orientation === "horizontal") return <HorizontalStepper steps={steps} current={current} />;
  return <VerticalStepper steps={steps} current={current} />;
}

function VerticalStepper({ steps, current }: { steps: Step[]; current: number }) {
  return (
    <ol className="space-y-1">
      {steps.map((s, i) => {
        const done = i < current;
        const active = i === current;
        const isLast = i === steps.length - 1;
        return (
          <li key={s.key} className="relative flex gap-3 pb-3">
            <div className="flex flex-col items-center">
              <div
                className={cn(
                  "flex h-9 w-9 shrink-0 items-center justify-center rounded-full border-2 text-sm font-semibold transition",
                  done && "border-primary bg-primary text-primary-foreground",
                  active && "border-primary text-primary ring-4 ring-primary/15",
                  !done && !active && "border-border text-muted-foreground",
                )}
              >
                {done ? <Check className="h-4 w-4" /> : i + 1}
              </div>
              {!isLast && (
                <div
                  className={cn(
                    "mt-1 w-0.5 flex-1 min-h-[24px]",
                    done ? "bg-primary" : "bg-border",
                  )}
                />
              )}
            </div>
            <div className="min-w-0 flex-1 pt-1.5">
              <div
                className={cn(
                  "text-sm font-medium leading-tight",
                  active ? "text-foreground" : done ? "text-foreground/90" : "text-muted-foreground",
                )}
              >
                {s.title}
              </div>
              {s.description && (
                <div className="mt-0.5 text-xs text-muted-foreground leading-snug">
                  {s.description}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function HorizontalStepper({ steps, current }: { steps: Step[]; current: number }) {
  return (
    <ol className="flex w-full items-center gap-2">
      {steps.map((s, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li key={s.key} className="flex flex-1 items-center gap-2 min-w-0">
            <div
              className={cn(
                "flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 text-sm font-semibold",
                done && "border-primary bg-primary text-primary-foreground",
                active && "border-primary text-primary",
                !done && !active && "border-border text-muted-foreground",
              )}
            >
              {done ? <Check className="h-4 w-4" /> : i + 1}
            </div>
            <div
              className={cn(
                "truncate text-xs font-medium",
                active ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {s.title}
            </div>
            {i < steps.length - 1 && (
              <div className={cn("h-0.5 flex-1", done ? "bg-primary" : "bg-border")} />
            )}
          </li>
        );
      })}
    </ol>
  );
}
