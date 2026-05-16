import * as React from "react";
import * as A from "@radix-ui/react-alert-dialog";
import { cn } from "@/lib/utils";
import { buttonVariants } from "./button";

export const AlertDialog = A.Root;
export const AlertDialogTrigger = A.Trigger;
export const AlertDialogPortal = A.Portal;

export const AlertDialogOverlay = React.forwardRef<
  React.ElementRef<typeof A.Overlay>,
  React.ComponentPropsWithoutRef<typeof A.Overlay>
>(({ className, ...props }, ref) => (
  <A.Overlay ref={ref} className={cn("fixed inset-0 z-50 bg-black/60 backdrop-blur-[1px]", className)} {...props} />
));
AlertDialogOverlay.displayName = "AlertDialogOverlay";

export const AlertDialogContent = React.forwardRef<
  React.ElementRef<typeof A.Content>,
  React.ComponentPropsWithoutRef<typeof A.Content>
>(({ className, ...props }, ref) => (
  <AlertDialogPortal>
    <AlertDialogOverlay />
    <A.Content
      ref={ref}
      className={cn(
        "fixed left-1/2 top-1/2 z-50 grid w-[min(92vw,440px)] -translate-x-1/2 -translate-y-1/2 gap-4",
        "rounded-md border border-border bg-card p-5 shadow-xl",
        className,
      )}
      {...props}
    />
  </AlertDialogPortal>
));
AlertDialogContent.displayName = "AlertDialogContent";

export const AlertDialogHeader = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex flex-col gap-1", className)} {...p} />
);
export const AlertDialogFooter = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex flex-col-reverse gap-2 sm:flex-row sm:justify-end", className)} {...p} />
);

export const AlertDialogTitle = React.forwardRef<
  React.ElementRef<typeof A.Title>,
  React.ComponentPropsWithoutRef<typeof A.Title>
>(({ className, ...props }, ref) => (
  <A.Title ref={ref} className={cn("text-base font-semibold tracking-tightish", className)} {...props} />
));
AlertDialogTitle.displayName = "AlertDialogTitle";

export const AlertDialogDescription = React.forwardRef<
  React.ElementRef<typeof A.Description>,
  React.ComponentPropsWithoutRef<typeof A.Description>
>(({ className, ...props }, ref) => (
  <A.Description ref={ref} className={cn("text-sm text-muted-foreground", className)} {...props} />
));
AlertDialogDescription.displayName = "AlertDialogDescription";

export const AlertDialogAction = React.forwardRef<
  React.ElementRef<typeof A.Action>,
  React.ComponentPropsWithoutRef<typeof A.Action>
>(({ className, ...props }, ref) => (
  <A.Action ref={ref} className={cn(buttonVariants({ variant: "destructive" }), className)} {...props} />
));
AlertDialogAction.displayName = "AlertDialogAction";

export const AlertDialogCancel = React.forwardRef<
  React.ElementRef<typeof A.Cancel>,
  React.ComponentPropsWithoutRef<typeof A.Cancel>
>(({ className, ...props }, ref) => (
  <A.Cancel ref={ref} className={cn(buttonVariants({ variant: "outline" }), className)} {...props} />
));
AlertDialogCancel.displayName = "AlertDialogCancel";
