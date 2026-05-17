# Confirm Dialog Reference

Promise-based replacement for `window.confirm`, rendered through an `AlertDialog` mounted once at app root.

**File:** `frontend/src/lib/confirm.tsx`

---

## 1. API

```ts
export interface ConfirmOptions {
  title?: string;            // default "Are you sure?"
  description?: string;       // default "" (hidden if empty)
  confirmText?: string;       // default "Confirm"
  cancelText?: string;        // default "Cancel"
  variant?: "default" | "destructive";  // default "default"
}

export function confirm(opts: ConfirmOptions = {}): Promise<boolean>;
export function ConfirmHost(): JSX.Element;
```

```tsx
const ok = await confirm({
  title: "Delete document?",
  description: "This cannot be undone.",
  confirmText: "Delete",
  cancelText: "Keep",
  variant: "destructive",
});
if (!ok) return;
await api.deleteDocument(id);
```

Resolves `true` for confirm, `false` for cancel/close.

---

## 2. Implementation

```tsx
import { useEffect, useState } from "react";
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogFooter,
  AlertDialogTitle, AlertDialogDescription, AlertDialogCancel, AlertDialogAction,
} from "@/components/ui/alert-dialog";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const DEFAULTS: Required<ConfirmOptions> = {
  title: "Are you sure?",
  description: "",
  confirmText: "Confirm",
  cancelText: "Cancel",
  variant: "default",
};

type State =
  | { open: false }
  | { open: true; opts: Required<ConfirmOptions>; resolve: (ok: boolean) => void };

let setState: ((s: State) => void) | null = null;

export function confirm(opts: ConfirmOptions = {}): Promise<boolean> {
  return new Promise((resolve) => {
    const merged: Required<ConfirmOptions> = { ...DEFAULTS, ...opts };
    if (setState) setState({ open: true, opts: merged, resolve });
    else resolve(false);                     // host not mounted — safe fallback
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
    <AlertDialog open={state.open} onOpenChange={(o) => { if (!o) close(false); }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{state.open ? state.opts.title : ""}</AlertDialogTitle>
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
                : undefined,                 // destructive uses the AlertDialog default
            )}
          >
            {state.open ? state.opts.confirmText : "Confirm"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
```

How it works:
- A module-level `setState` ref is set when `<ConfirmHost />` mounts.
- `confirm()` captures the promise resolver and pushes state into the host.
- User clicks Action / Cancel / closes the dialog → `close(true|false)` resolves and clears state.
- Calling `confirm()` before the host is mounted resolves `false` (defensive default — avoids hanging promises).

---

## 3. Mount

```tsx
// frontend/src/App.tsx
import { ConfirmHost } from "@/lib/confirm";
import { UnsavedChangesDialog } from "@/components/UnsavedChangesDialog";

export default function App() {
  return (
    <>
      {/* …pages… */}
      <ConfirmHost />
      <UnsavedChangesDialog />
      <ActiveJobsBanner />
    </>
  );
}
```

Mount once. Pages anywhere can call `confirm({...})`.

---

## 4. Variant convention

- `variant: "default"` — confirm button gets `buttonVariants({ variant: "default" })` (solid primary).
- `variant: "destructive"` — relies on `AlertDialogAction`'s default styling, which is destructive (red) — see [Design System §5.9](./design-system.md#59-alertdialog-wraps-radix).

Always use `destructive` for: deletes, cancellations of long jobs, clearing logs, switching embedding model, applying a preset that overwrites user edits.

---

## 5. Focus + a11y

Handled by Radix's `AlertDialog`:
- Focus trap inside the dialog.
- Default focus goes to the Cancel button (safer for destructive flows).
- Escape closes (resolves `false`).
- Click outside closes (`onOpenChange`).
- `role="alertdialog"` semantics.

---

## 6. Idioms

✅ **Do:**
- Wrap every destructive action in `confirm({ variant: "destructive" })`.
- Use the description to explain irreversibility ("This cannot be undone.", "An in-flight LLM call finishes first.").
- Customize `confirmText` to the verb ("Delete", "Cancel job", "Discard").

❌ **Don't:**
- Stack confirms (calling `confirm()` from inside another `confirm`). Resolve the first before showing a second.
- Use it for routine UI choices (mode picker, dropdown). Reserve for **side effects you might regret**.
- Use `window.confirm` — blocks the main thread and breaks the design system.

---

## 7. Comparison with the unsaved guard

| Use case | Tool |
|---|---|
| Stop unsaved nav | [`useUnsavedGuard(dirty)`](./hash-routing.md) — automatic |
| Confirm a destructive action | `await confirm({...})` |

Both render via the same `AlertDialog` primitive, but have separate hosts (`<UnsavedChangesDialog />` for the guard, `<ConfirmHost />` for `confirm()`). Mount both at root.
