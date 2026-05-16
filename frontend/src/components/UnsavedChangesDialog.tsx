import { useEffect, useState } from "react";
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle,
  AlertDialogDescription, AlertDialogFooter, AlertDialogAction, AlertDialogCancel,
} from "@/components/ui/alert-dialog";
import { guard } from "@/lib/unsavedGuard";

export function UnsavedChangesDialog() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const tick = () => setOpen(guard.hasPending());
    const unsub = guard.subscribe(tick);
    return () => { unsub(); };
  }, []);

  return (
    <AlertDialog
      open={open}
      onOpenChange={(o) => {
        if (!o) guard.cancelPending();
      }}
    >
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
