import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

export interface Step {
  key: string;
  title: string;
  description?: string;
}

export function Stepper({ steps, current }: { steps: Step[]; current: number }) {
  return (
    <ol className="flex w-full items-start gap-2">
      {steps.map((s, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li key={s.key} className="flex flex-1 items-start gap-3">
            <div className="flex flex-col items-center">
              <div
                className={cn(
                  "flex h-9 w-9 items-center justify-center rounded-full border-2 text-sm font-semibold transition",
                  done && "border-primary bg-primary text-primary-foreground",
                  active && "border-primary text-primary",
                  !done && !active && "border-border text-muted-foreground",
                )}
              >
                {done ? <Check className="h-4 w-4" /> : i + 1}
              </div>
              {i < steps.length - 1 && (
                <div className={cn("mt-1 h-8 w-0.5", done ? "bg-primary" : "bg-border")} />
              )}
            </div>
            <div className="pt-1">
              <div className={cn("text-sm font-medium", active ? "text-foreground" : "text-muted-foreground")}>
                {s.title}
              </div>
              {s.description && <div className="text-xs text-muted-foreground">{s.description}</div>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
