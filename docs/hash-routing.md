# Hash Routing + Unsaved Guard Reference

No router library. `window.location.hash` + a custom hook + an in-memory singleton guard. Deep-link params live in the query portion of the hash; pages parse them themselves.

**Files:**
- `frontend/src/components/AppShell.tsx` — `useHashRoute` + nav guard
- `frontend/src/lib/unsavedGuard.ts` — `guard` singleton + `useUnsavedGuard`
- `frontend/src/components/UnsavedChangesDialog.tsx` — global confirm host

---

## 1. Hash format

```
#/route                      → just the route
#/route?key=value&key2=v2    → route + query
#/jobs?id=abc123             → deep link to a specific item on the page
```

The hook only extracts the route. Query params are read by individual pages via `window.location.hash.split("?")[1]` or `window.location.search`.

---

## 2. `useHashRoute` hook

```ts
function routeFromHash(): Route {
  const h = window.location.hash.replace(/^#\/?/, "").split(/[?#]/)[0];
  return (NAV.find((n) => n.key === h)?.key ?? "documents") as Route;
}

export function useHashRoute(): [Route, (r: Route) => void] {
  const [r, setR] = useState<Route>(routeFromHash());

  useEffect(() => {
    let last = window.location.hash;
    const onChange = () => {
      const next = routeFromHash();
      if (guard.isDirty()) {
        // Browser back/forward fired while unsaved — revert until user decides
        const target = next;
        window.history.replaceState(null, "", last);
        guard.tryNavigate(() => {
          window.location.hash = `#/${target}`;
          setR(target);
        });
        return;
      }
      last = window.location.hash;
      setR(next);
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const set = (next: Route) => {
    window.location.hash = `#/${next}`;
    setR(next);
  };

  return [r, set];
}
```

Behavior:
- On mount: parse current hash.
- `hashchange` listener: if clean, accept; if dirty, **revert** and route via `guard.tryNavigate` (which opens the confirm dialog).
- `set(next)` directly mutates the hash — for in-app nav buttons, **always go through** `guard.tryNavigate(() => setRoute(...))` so unsaved guards fire (see AppShell wire-up below).

Defaults to `"documents"` if hash is empty or unknown.

---

## 3. Wire-up in AppShell

```tsx
const [route, setRoute] = useHashRoute();

<button onClick={() => guard.tryNavigate(() => setRoute(item.key))}>
  {item.label}
</button>
```

The nav handler routes through `tryNavigate` so a dirty page intercepts. Browser back/forward handled by the hook internally.

---

## 4. Unsaved guard singleton

```ts
// frontend/src/lib/unsavedGuard.ts
class Guard {
  private dirty = false;
  private pending: (() => void) | null = null;
  private subs = new Set<() => void>();

  setDirty(v: boolean) {
    if (this.dirty === v) return;
    this.dirty = v;
    this.notify();
  }

  isDirty() { return this.dirty; }
  hasPending() { return this.pending !== null; }

  tryNavigate(action: () => void) {
    if (!this.dirty) { action(); return; }
    this.pending = action;
    this.notify();
  }

  confirmPending() {
    const a = this.pending;
    this.pending = null;
    this.dirty = false;
    this.notify();
    a?.();
  }

  cancelPending() {
    this.pending = null;
    this.notify();
  }

  subscribe(cb: () => void) {
    this.subs.add(cb);
    return () => this.subs.delete(cb);
  }

  private notify() {
    this.subs.forEach(cb => cb());
  }
}

export const guard = new Guard();

export function useUnsavedGuard(dirty: boolean) {
  useEffect(() => {
    guard.setDirty(dirty);
    if (!dirty) return;
    const onUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onUnload);
    return () => {
      window.removeEventListener("beforeunload", onUnload);
      guard.setDirty(false);
    };
  }, [dirty]);
}
```

`beforeunload` listener triggers the browser's native "Leave site?" prompt on tab close / refresh while dirty.

---

## 5. UnsavedChangesDialog

```tsx
export function UnsavedChangesDialog() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const tick = () => setOpen(guard.hasPending());
    const unsub = guard.subscribe(tick);
    tick();
    return () => { unsub(); };
  }, []);

  return (
    <AlertDialog open={open} onOpenChange={(o) => { if (!o) guard.cancelPending(); }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
          <AlertDialogDescription>
            You have edits that have not been saved. Leaving this page will lose them.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={() => guard.cancelPending()}>Stay on page</AlertDialogCancel>
          <AlertDialogAction onClick={() => guard.confirmPending()}>Discard & leave</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
```

Mount once at app root.

---

## 6. Page usage

```tsx
const [name, setName] = useState(initialName);
const dirty = name !== initialName;
useUnsavedGuard(dirty);

return <Input value={name} onChange={(e) => setName(e.target.value)} />;
```

That's the whole API. Pages don't need to know about the dialog or singleton.

---

## 7. Deep-link params

For `#/jobs?id=abc`, parse manually:

```tsx
useEffect(() => {
  const q = new URLSearchParams(window.location.hash.split("?")[1] || "");
  const id = q.get("id");
  if (id) setSelectedId(id);
}, []);
```

Also listen to `hashchange` if you want live updates when navigation changes the param without changing the route.

---

## 8. Adding a route

1. Add a `{ key, label, icon }` entry to `NAV` in `AppShell.tsx`.
2. Add a `<Page>` import + render branch in `App.tsx`.
3. The hook accepts the new key automatically (defaulted in `routeFromHash`).

No central router config, no codegen. Cost of a new page = 2 lines + the page itself.
