/**
 * Global confirm() replacement using shadcn AlertDialog.
 *
 * Usage:
 *   const ok = await confirm({ title: "Delete?", description: "...", variant: "destructive" });
 *   if (!ok) return;
 *
 * Mount <ConfirmHost /> once at the app root.
 */
import { useEffect, useState } from "react";
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle,
  AlertDialogDescription, AlertDialogFooter, AlertDialogAction, AlertDialogCancel,
} from "@/components/ui/alert-dialog";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";

export interface ConfirmOptions {
  title?: string;
  description?: string;
  confirmText?: string;
  cancelText?: string;
  variant?: "default" | "destructive";
}

type Resolver = (ok: boolean) => void;

type State =
  | { open: false }
  | { open: true; opts: Required<ConfirmOptions>; resolve: Resolver };

let setState: ((s: State) => void) | null = null;

const DEFAULTS: Required<ConfirmOptions> = {
  title: "Are you sure?",
  description: "",
  confirmText: "Confirm",
  cancelText: "Cancel",
  variant: "default",
};

export function confirm(opts: ConfirmOptions = {}): Promise<boolean> {
  return new Promise((resolve) => {
    const merged: Required<ConfirmOptions> = { ...DEFAULTS, ...opts };
    if (setState) setState({ open: true, opts: merged, resolve });
    else resolve(false); // host not mounted yet — safe fallback
  });
}

export function ConfirmHost() {
  const [state, _setState] = useState<State>({ open: false });

  useEffect(() => {
    setState = _setState;
    return () => { setState = null; };
  }, []);

  const close = (ok: boolean) => {
    if (!state.open) return;
    state.resolve(ok);
    _setState({ open: false });
  };

  return (
    <AlertDialog
      open={state.open}
      onOpenChange={(o) => { if (!o) close(false); }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {state.open ? state.opts.title : ""}
          </AlertDialogTitle>
          {state.open && state.opts.description && (
            <AlertDialogDescription>{state.opts.description}</AlertDialogDescription>
          )}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={() => close(false)}>
            {state.open ? state.opts.cancelText : "Cancel"}
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={() => close(true)}
            className={cn(
              state.open && state.opts.variant === "default"
                ? buttonVariants({ variant: "default" })
                : undefined,
            )}
          >
            {state.open ? state.opts.confirmText : "Confirm"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
