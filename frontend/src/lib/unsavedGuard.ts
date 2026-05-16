/**
 * Cross-component unsaved-changes guard.
 *
 * A page calls `useUnsavedGuard(isDirty)` to register itself as dirty. The
 * app shell calls `tryNavigate(action)` whenever the user wants to move
 * away — if anyone is dirty, a confirm dialog is opened first; otherwise
 * the action runs immediately.
 *
 * Browser refresh / tab close is handled by the `beforeunload` listener
 * registered automatically while dirty.
 */
import { useEffect } from "react";

type Listener = () => void;

class Guard {
  private dirty = false;
  private pending: (() => void) | null = null;
  private subs = new Set<Listener>();

  setDirty(v: boolean) {
    if (this.dirty === v) return;
    this.dirty = v;
    this.notify();
  }
  isDirty() { return this.dirty; }

  /** Open the confirm dialog (or run immediately if clean). */
  tryNavigate(action: () => void) {
    if (!this.dirty) { action(); return; }
    this.pending = action;
    this.notify();
  }
  hasPending() { return this.pending !== null; }
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

  subscribe(fn: Listener) {
    this.subs.add(fn);
    return () => this.subs.delete(fn);
  }
  private notify() { this.subs.forEach((f) => f()); }
}

export const guard = new Guard();

// Page hook: marks the guard dirty while `dirty` is true.
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
