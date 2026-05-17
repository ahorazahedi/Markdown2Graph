# Design System Reference

A portable reference of the React frontend design system used in this project. Copy this doc into other projects to recreate the same look-and-feel.

**Stack:** React 18 + TypeScript + Vite + Tailwind CSS 3 + Radix UI primitives + class-variance-authority + lucide-react + tailwind-merge + clsx.

---

## 1. Design Philosophy

- **Monochromatic + semantic accents.** Primary surface is navy/white; color (red/green/amber) only carries meaning (status, destructive action).
- **HSL custom properties.** Every color is a CSS var so dark mode is a token re-map, not branched components.
- **Tight density.** Small radii (4px base), hairline 1px borders, compact heights (h-8 = 32px), small base font (text-sm = 14px), smallest text 11px (`text-2xs`).
- **Slightly tightened tracking.** `tracking-tightish` = `-0.005em` everywhere a title or button label appears.
- **System fonts.** No webfont; native UI font stack with OpenType features `cv02 cv03 cv04 cv11` for crisper rendering.
- **Composable shadcn-style primitives.** No external component lib — every primitive is in `src/components/ui/*` and uses CVA for variants and `cn()` for class merging.

---

## 2. Design Tokens

**File:** `src/index.css`

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --background: 0 0% 100%;
    --foreground: 222 24% 12%;
    --card: 0 0% 100%;
    --card-foreground: 222 24% 12%;
    --primary: 222 47% 11%;
    --primary-foreground: 0 0% 100%;
    --secondary: 220 14% 96%;
    --secondary-foreground: 222 24% 12%;
    --muted: 220 14% 96%;
    --muted-foreground: 220 9% 46%;
    --accent: 220 14% 94%;
    --accent-foreground: 222 24% 12%;
    --destructive: 0 72% 51%;
    --destructive-foreground: 0 0% 100%;
    --success: 142 71% 38%;
    --warning: 38 92% 50%;
    --border: 220 13% 91%;
    --input: 220 13% 91%;
    --ring: 220 14% 70%;
    --radius: 0.25rem;
  }
  .dark {
    --background: 222 24% 6%;
    --foreground: 220 14% 96%;
    --card: 222 24% 8%;
    --card-foreground: 220 14% 96%;
    --primary: 220 14% 96%;
    --primary-foreground: 222 24% 8%;
    --secondary: 222 18% 14%;
    --secondary-foreground: 220 14% 96%;
    --muted: 222 18% 14%;
    --muted-foreground: 220 9% 62%;
    --accent: 222 18% 16%;
    --accent-foreground: 220 14% 96%;
    --destructive: 0 70% 45%;
    --destructive-foreground: 0 0% 100%;
    --success: 142 65% 42%;
    --warning: 38 92% 55%;
    --border: 222 16% 18%;
    --input: 222 16% 18%;
    --ring: 222 16% 30%;
  }
  html, body, #root { height: 100%; }
  body {
    @apply bg-background text-foreground antialiased;
    overflow: hidden;                /* shell handles scrolling per-region */
    font-family: ui-sans-serif, system-ui, -apple-system, "Inter", "SF Pro Text",
      "Segoe UI", Roboto, sans-serif;
    font-feature-settings: "cv02", "cv03", "cv04", "cv11";
    text-rendering: optimizeLegibility;
  }
  *::-webkit-scrollbar { width: 8px; height: 8px; }
  *::-webkit-scrollbar-thumb { @apply bg-border rounded-sm; }
  *::-webkit-scrollbar-thumb:hover { @apply bg-muted-foreground/30; }
  hr { @apply border-border; }
}

@layer utilities {
  .text-xxs { font-size: 0.6875rem; line-height: 1rem; }
  .hairline { border-color: hsl(var(--border)); }
}

:where(button, [role="button"], input, textarea, select):focus-visible {
  outline: 1px solid hsl(var(--ring));
  outline-offset: 1px;
}
```

### Token rationale

| Token | Light HSL | Dark HSL | Use |
|-------|-----------|----------|-----|
| `background` | `0 0% 100%` | `222 24% 6%` | Page surface |
| `foreground` | `222 24% 12%` | `220 14% 96%` | Default text |
| `card` | `0 0% 100%` | `222 24% 8%` | Raised surface (subtly lighter than bg in dark) |
| `primary` | `222 47% 11%` | `220 14% 96%` | Solid action button, **inverted** in dark |
| `secondary` / `muted` | `220 14% 96%` | `222 18% 14%` | Subtle fills, table stripes |
| `accent` | `220 14% 94%` | `222 18% 16%` | Hover background |
| `destructive` | `0 72% 51%` | `0 70% 45%` | Delete / danger |
| `success` | `142 71% 38%` | `142 65% 42%` | OK badges |
| `warning` | `38 92% 50%` | `38 92% 55%` | Running / attention |
| `border` / `input` | `220 13% 91%` | `222 16% 18%` | Hairline borders |
| `ring` | `220 14% 70%` | `222 16% 30%` | Focus outline |
| `--radius` | `0.25rem` | — | Base radius (sm = 3px, md = 4px, lg = 6px) |

Dark mode is enabled by adding `class="dark"` to `<html>`. All components re-theme automatically — never branch on a `theme` prop.

---

## 3. Tailwind Config

**File:** `tailwind.config.js`

```js
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: { center: true, padding: "1.5rem", screens: { "2xl": "1320px" } },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        destructive: { DEFAULT: "hsl(var(--destructive))", foreground: "hsl(var(--destructive-foreground))" },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
      },
      borderRadius: {
        lg: "calc(var(--radius) + 2px)",   /* 6px */
        md: "var(--radius)",               /* 4px */
        sm: "calc(var(--radius) - 1px)",   /* 3px */
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }],
      },
      letterSpacing: {
        tightish: "-0.005em",
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
```

---

## 4. Utility: `cn()`

**File:** `src/lib/utils.ts`

```ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

Every primitive uses `cn(baseClasses, className)` so callers can override conflicting Tailwind utilities cleanly. **Required.**

---

## 5. UI Primitives

All live in `src/components/ui/*`. Each uses `forwardRef`, CVA for variants where helpful, and `cn()` for merging.

### 5.1 Button

```tsx
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-sm text-sm font-medium tracking-tightish transition-colors disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
        outline: "border border-border bg-transparent hover:bg-accent",
        secondary: "bg-secondary text-secondary-foreground hover:bg-accent",
        ghost: "hover:bg-accent",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-8 px-3",
        sm: "h-7 px-2.5 text-2xs",
        lg: "h-10 px-5",
        icon: "h-8 w-8",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size, className }))} {...props} />
  ),
);
Button.displayName = "Button";

export { buttonVariants };
```

- 6 variants × 4 sizes.
- Always `gap-1.5` so icons sit naturally next to text.
- Disabled = `opacity-50` + `pointer-events-none`.
- `whitespace-nowrap` — buttons never wrap.

### 5.2 Card

```tsx
export const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref}
      className={cn("rounded-md border border-border bg-card text-card-foreground", className)}
      {...props}/>
  ),
);

export const CardHeader = ({ className, ...p }) => (
  <div className={cn("flex flex-col gap-1 px-5 py-4 border-b border-border", className)} {...p} />
);
export const CardTitle = ({ className, ...p }) => (
  <h3 className={cn("text-base font-semibold tracking-tightish", className)} {...p} />
);
export const CardDescription = ({ className, ...p }) => (
  <p className={cn("text-sm text-muted-foreground leading-snug", className)} {...p} />
);
export const CardContent = ({ className, ...p }) => (
  <div className={cn("px-5 py-4", className)} {...p} />
);
export const CardFooter = ({ className, ...p }) => (
  <div className={cn("flex items-center px-5 py-3 border-t border-border", className)} {...p} />
);
```

Compose: `Card > CardHeader (Title + Description) + CardContent + CardFooter (optional)`. Always `px-5`, body `py-4`, footer `py-3`.

### 5.3 Badge

```tsx
type Variant = "default" | "secondary" | "outline" | "destructive" | "success" | "warning";

export function Badge({ className, variant = "default", ...props }: React.HTMLAttributes<HTMLSpanElement> & { variant?: Variant }) {
  const styles: Record<Variant, string> = {
    default: "bg-secondary text-secondary-foreground",
    secondary: "bg-muted text-muted-foreground",
    outline: "border border-border text-foreground",
    destructive: "bg-destructive/15 text-destructive border border-destructive/30",
    success: "bg-[hsl(var(--success))]/15 text-[hsl(var(--success))] border border-[hsl(var(--success))]/30",
    warning: "bg-[hsl(var(--warning))]/15 text-[hsl(var(--warning))] border border-[hsl(var(--warning))]/30",
  };
  return (
    <span
      className={cn("inline-flex items-center rounded-sm px-1.5 py-0.5 text-2xs font-medium tracking-tight",
        styles[variant], className)}
      {...props}
    />
  );
}
```

`text-2xs` (11px), 15%/30% alpha for the colored variants — readable but never shouty.

### 5.4 Input

```tsx
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input ref={ref} type={type}
      className={cn(
        "flex h-8 w-full rounded-sm border border-border bg-background px-2.5 text-sm",
        "placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}/>
  ),
);
```

`h-8` to match buttons; sits flush in form rows.

### 5.5 Textarea

```tsx
export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea ref={ref}
      className={cn(
        "flex min-h-[72px] w-full rounded-sm border border-border bg-background px-2.5 py-1.5 text-sm",
        "placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}/>
  ),
);
```

### 5.6 Label (wraps Radix)

```tsx
import * as L from "@radix-ui/react-label";

export const Label = React.forwardRef<
  React.ElementRef<typeof L.Root>,
  React.ComponentPropsWithoutRef<typeof L.Root>
>(({ className, ...props }, ref) => (
  <L.Root ref={ref} className={cn("text-sm font-medium leading-none", className)} {...props} />
));
```

### 5.7 Progress (wraps Radix)

```tsx
import * as P from "@radix-ui/react-progress";

export const Progress = React.forwardRef<
  React.ElementRef<typeof P.Root>,
  React.ComponentPropsWithoutRef<typeof P.Root> & { value?: number }
>(({ className, value = 0, ...props }, ref) => (
  <P.Root ref={ref}
    className={cn("relative h-1.5 w-full overflow-hidden rounded-sm bg-muted", className)}
    {...props}>
    <P.Indicator
      className="h-full w-full flex-1 bg-foreground/80 transition-all"
      style={{ transform: `translateX(-${100 - Math.min(100, Math.max(0, value))}%)` }}
    />
  </P.Root>
));
```

`h-1.5` (6px) by default; banners override to `h-1`.

### 5.8 Tabs (wraps Radix)

```tsx
import * as T from "@radix-ui/react-tabs";

export const Tabs = T.Root;

export const TabsList = React.forwardRef<...>(({ className, ...props }, ref) => (
  <T.List ref={ref}
    className={cn("inline-flex items-center gap-1 border-b border-border", className)}
    {...props}/>
));

export const TabsTrigger = React.forwardRef<...>(({ className, ...props }, ref) => (
  <T.Trigger ref={ref}
    className={cn(
      "relative h-8 px-3 text-sm text-muted-foreground hover:text-foreground transition-colors",
      "data-[state=active]:text-foreground",
      "data-[state=active]:after:absolute data-[state=active]:after:left-0 data-[state=active]:after:right-0 data-[state=active]:after:-bottom-px data-[state=active]:after:h-px data-[state=active]:after:bg-foreground",
      className,
    )}
    {...props}/>
));

export const TabsContent = T.Content;
```

Indicator is a 1px `::after` bar — no animated underline pill.

### 5.9 AlertDialog (wraps Radix)

Centered modal, overlay `bg-black/60 backdrop-blur-[1px]`, content max width `min(92vw, 440px)`, `rounded-md`, `shadow-xl`.

```tsx
export const AlertDialog = A.Root;
export const AlertDialogTrigger = A.Trigger;
export const AlertDialogPortal = A.Portal;

export const AlertDialogOverlay = React.forwardRef<...>(({ className, ...props }, ref) => (
  <A.Overlay ref={ref}
    className={cn("fixed inset-0 z-50 bg-black/60 backdrop-blur-[1px]", className)}
    {...props}/>
));

export const AlertDialogContent = React.forwardRef<...>(({ className, ...props }, ref) => (
  <AlertDialogPortal>
    <AlertDialogOverlay />
    <A.Content ref={ref}
      className={cn(
        "fixed left-1/2 top-1/2 z-50 grid w-[min(92vw,440px)] -translate-x-1/2 -translate-y-1/2 gap-4",
        "rounded-md border border-border bg-card p-5 shadow-xl",
        className,
      )}
      {...props}/>
  </AlertDialogPortal>
));

export const AlertDialogHeader = ({ className, ...p }) => (
  <div className={cn("flex flex-col gap-1", className)} {...p} />
);
export const AlertDialogFooter = ({ className, ...p }) => (
  <div className={cn("flex flex-col-reverse gap-2 sm:flex-row sm:justify-end", className)} {...p} />
);
export const AlertDialogTitle = React.forwardRef<...>(({ className, ...props }, ref) => (
  <A.Title ref={ref} className={cn("text-base font-semibold tracking-tightish", className)} {...props} />
));
export const AlertDialogDescription = React.forwardRef<...>(({ className, ...props }, ref) => (
  <A.Description ref={ref} className={cn("text-sm text-muted-foreground", className)} {...props} />
));
export const AlertDialogAction = React.forwardRef<...>(({ className, ...props }, ref) => (
  <A.Action ref={ref} className={cn(buttonVariants({ variant: "destructive" }), className)} {...props} />
));
export const AlertDialogCancel = React.forwardRef<...>(({ className, ...props }, ref) => (
  <A.Cancel ref={ref} className={cn(buttonVariants({ variant: "outline" }), className)} {...props} />
));
```

Action defaults to destructive variant, Cancel to outline. Footer reverses on mobile (action on top).

### 5.10 Drawer (custom, side panel)

Slide-from-right panel, Esc to close, overlay closes on click.

```tsx
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export function Drawer({
  open, onClose, title, subtitle, children, footer,
  width = "max-w-3xl",
}: {
  open: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  children: React.ReactNode;
  footer?: React.ReactNode;
  width?: string;
}) {
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/40 backdrop-blur-[1px]" onClick={onClose} />
      <aside className={cn("flex h-full w-full flex-col border-l border-border bg-background", width)}>
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-border px-5 py-3">
          <div className="min-w-0">
            {title && <div className="truncate text-sm font-semibold tracking-tightish">{title}</div>}
            {subtitle && <div className="truncate text-xs text-muted-foreground">{subtitle}</div>}
          </div>
          <button onClick={onClose}
            className="rounded-sm p-1 text-muted-foreground hover:bg-accent"
            aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="flex-1 overflow-auto">{children}</div>
        {footer && <footer className="shrink-0 border-t border-border px-5 py-3">{footer}</footer>}
      </aside>
    </div>
  );
}
```

### 5.11 SearchableSelect (custom)

Combobox with: type-to-filter, click-outside close, Esc close, optional `allowCustom` (Enter to add a new value), optional `onRefresh` button. Trigger styled exactly like `Input` (`h-8`, `border-border`, `bg-background`). Dropdown: `max-h-72 overflow-auto`, items use `hover:bg-accent/60` with a checkmark for the active option.

Props:
```ts
interface SearchableOption { value: string; label: string; hint?: string; }

interface Props {
  value: string;
  onChange: (v: string) => void;
  options: SearchableOption[];
  placeholder?: string;
  loading?: boolean;
  onRefresh?: () => void;
  emptyText?: string;
  disabled?: boolean;
  allowCustom?: boolean;
}
```

---

## 6. Shell + Layout

### 6.1 AppShell — sidebar + main grid

```tsx
<div className="grid h-screen grid-cols-[220px_minmax(0,1fr)] overflow-hidden bg-background">
  <aside className="flex h-screen flex-col overflow-hidden border-r border-border bg-card/40">
    <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
      <Activity className="h-4 w-4" />
      <span className="text-sm font-semibold tracking-tightish">app-name</span>
    </div>
    <nav className="flex-1 overflow-y-auto px-2 py-2">
      {/* nav items: rounded-sm px-2 py-1.5 text-sm. active = bg-accent text-foreground. inactive = text-muted-foreground hover:bg-accent/60 hover:text-foreground */}
    </nav>
  </aside>
  <main className="h-screen min-w-0 overflow-hidden">{children}</main>
</div>
```

- Sidebar fixed at **220px**, bg `card/40` (slight transparency over card).
- Sidebar header h-12 (48px).
- Nav items h-auto, `gap-2`, `text-sm`, icons `h-4 w-4`.
- Main is `overflow-hidden` — children own their own scrolling.

### 6.2 PageContainer

```tsx
export function PageContainer({
  children, header, className, maxWidth = "max-w-6xl",
}: {
  children: React.ReactNode;
  header?: React.ReactNode;
  className?: string;
  maxWidth?: string;
}) {
  return (
    <div className="flex h-full flex-col">
      {header && (
        <div className="shrink-0 border-b border-border bg-card/30 px-8 py-4">{header}</div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className={cn("mx-auto px-8 py-6", maxWidth, className)}>{children}</div>
      </div>
    </div>
  );
}
```

- Full-bleed header band (`bg-card/30`, `px-8 py-4`, bottom border).
- Body scrolls, width-constrained to `max-w-6xl` (1280px) by default, padded `px-8 py-6`.

### 6.3 PageHeader

```tsx
export function PageHeader({ title, description, actions }: {
  title: string; description?: string; actions?: React.ReactNode;
}) {
  return (
    <header className="flex items-end justify-between gap-6">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tightish">{title}</h1>
        {description && <p className="mt-1 text-xs text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}
```

- Title `text-lg` (18px). Description `text-xs` (12px) muted.
- `items-end` so action buttons sit on the same baseline as the title.

### 6.4 StatusBadge

```tsx
export function DocumentStatusBadge({ status }: { status: string }) {
  switch (status) {
    case "completed":  return <Badge variant="success">completed</Badge>;
    case "processing": return <Badge variant="warning">processing</Badge>;
    case "failed":     return <Badge variant="destructive">failed</Badge>;
    case "pending":    return <Badge variant="outline">pending</Badge>;
    default:           return <Badge variant="secondary">{status}</Badge>;
  }
}
```

Color mapping convention: `success` → done, `warning` → in-flight, `destructive` → failed, `outline` → pending, `secondary` → unknown.

---

## 7. Global Patterns

### 7.1 `cn` + tailwind-merge

Already covered (§4). Always use it.

### 7.2 Global `confirm()`

Promise-based replacement for `window.confirm`, rendered through an AlertDialog mounted once at the app root via `<ConfirmHost />`.

```ts
const ok = await confirm({
  title: "Delete document?",
  description: "This cannot be undone.",
  confirmText: "Delete",
  cancelText: "Keep",
  variant: "destructive",
});
if (ok) { /* go */ }
```

### 7.3 Unsaved guard

`src/lib/unsavedGuard.ts` exports `guard` (singleton) + `useUnsavedGuard(dirty)`. While dirty, `beforeunload` is blocked and `guard.tryNavigate(action)` opens an AlertDialog asking the user to confirm discarding edits. Mount `<UnsavedChangesDialog />` once at the app root. AppShell calls `guard.tryNavigate` around every route change.

### 7.4 Hash routing

`useHashRoute()` returns `[route, setRoute]` driven by `window.location.hash` (`#/jobs`, `#/documents`, …). No router library — small surface, integrates cleanly with the unsaved guard.

### 7.5 Toast / status surfacing

This codebase doesn't ship a toast; transient status is shown inline on cards or in the bottom-right banner. If you add one, use `@radix-ui/react-toast` (already in deps) and theme via the same tokens.

### 7.6 react-flow theming

If you use `@xyflow/react`, copy these rules so edges/controls/minimap follow the design tokens:

```css
.react-flow__edge-textbg { fill: hsl(var(--card)); }
.react-flow__edge-text   { fill: hsl(var(--foreground)); font-size: 10px; }
.react-flow__controls,
.react-flow__controls-button {
  background: hsl(var(--card));
  color: hsl(var(--foreground));
  border-color: hsl(var(--border));
}
.react-flow__minimap { background: hsl(var(--card)); }
.react-flow__attribution { display: none; }
```

---

## 8. Composition Patterns (real-world recipes)

### 8.1 Standard page

```tsx
<PageContainer
  header={
    <PageHeader
      title="Ingest"
      description="Run extraction over selected documents"
      actions={
        <>
          <Button size="sm" variant="outline" onClick={refresh}>Refresh</Button>
          <Button size="sm" onClick={runPending}>Run pending ({n})</Button>
        </>
      }
    />
  }
>
  <Card>
    <CardHeader>
      <CardTitle>Documents</CardTitle>
      <CardDescription>Select which to process</CardDescription>
    </CardHeader>
    <CardContent>{/* table */}</CardContent>
  </Card>

  <Card>
    <CardHeader>
      <CardTitle>Guidance</CardTitle>
    </CardHeader>
    <CardContent>
      <Textarea value={g} onChange={(e) => setG(e.target.value)} />
    </CardContent>
    <CardFooter>
      <Button size="sm" disabled={!dirty}>Save</Button>
    </CardFooter>
  </Card>
</PageContainer>
```

### 8.2 Two-pane (list + detail)

Used by `JobsPage` and `LLMCallsPage`. Skip `PageContainer`, render directly under `<main>`:

```tsx
<div className="flex h-full">
  <div className="w-[340px] shrink-0 overflow-y-auto border-r border-border">
    {items.map((it) => (
      <button
        key={it.id}
        onClick={() => setSelected(it.id)}
        className={cn(
          "w-full text-left px-3 py-2 border-b border-border",
          selected === it.id ? "bg-accent" : "hover:bg-accent/60",
        )}
      >
        {/* row */}
      </button>
    ))}
  </div>
  <div className="flex-1 overflow-y-auto">{/* detail */}</div>
</div>
```

### 8.3 Settings — left nav tabs

```tsx
<PageContainer header={<PageHeader title="Settings" />}>
  <div className="flex gap-8">
    <nav className="w-48 shrink-0 space-y-1">
      {SECTIONS.map((s) => (
        <button
          key={s.key}
          onClick={() => setSection(s.key)}
          className={cn(
            "w-full text-left px-3 py-2 rounded-sm text-sm",
            section === s.key ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60",
          )}
        >
          {s.label}
        </button>
      ))}
    </nav>
    <div className="flex-1 min-w-0">{/* current section */}</div>
  </div>
</PageContainer>
```

### 8.4 Drawer for detail

```tsx
<Drawer open={!!open} onClose={() => setOpen(null)} title={open?.name} width="max-w-4xl">
  <Tabs defaultValue="content">
    <TabsList>
      <TabsTrigger value="content">Content</TabsTrigger>
      <TabsTrigger value="metadata">Metadata</TabsTrigger>
    </TabsList>
    <TabsContent value="content">{/* ... */}</TabsContent>
    <TabsContent value="metadata">{/* ... */}</TabsContent>
  </Tabs>
</Drawer>
```

### 8.5 Floating status banner

See `ActiveJobsBanner` — bottom-right card stack (`fixed bottom-3 right-3 z-40 w-[min(420px,calc(100vw-1.5rem))]`), each card `rounded-md border bg-card shadow-lg`, with a thin `h-1 rounded-none` Progress strip at the bottom.

---

## 9. Stack & Dependencies

`package.json` essentials:

```json
{
  "dependencies": {
    "@radix-ui/react-alert-dialog": "^1.1.2",
    "@radix-ui/react-dialog": "^1.1.2",
    "@radix-ui/react-label": "^2.1.0",
    "@radix-ui/react-progress": "^1.1.0",
    "@radix-ui/react-slot": "^1.1.0",
    "@radix-ui/react-tabs": "^1.1.1",
    "@radix-ui/react-toast": "^1.2.2",
    "@xyflow/react": "^12.3.5",
    "class-variance-authority": "^0.7.0",
    "clsx": "^2.1.1",
    "lucide-react": "^0.454.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "tailwind-merge": "^2.5.4"
  },
  "devDependencies": {
    "@tailwindcss/typography": "^0.5.15",
    "tailwindcss": "^3.4.14",
    "typescript": "^5.6.3",
    "vite": "^5.4.10"
  }
}
```

---

## 10. Quick port checklist

1. Copy `src/index.css` + `tailwind.config.js`.
2. Copy `src/lib/utils.ts` (`cn`).
3. Install deps from §9.
4. Copy `src/components/ui/*` and `src/components/{AppShell, PageContainer, PageHeader, StatusBadge, UnsavedChangesDialog}.tsx`.
5. Copy `src/lib/{confirm.tsx, unsavedGuard.ts}`.
6. Mount `<ConfirmHost />` and `<UnsavedChangesDialog />` at app root.
7. Use `PageContainer + PageHeader + Card*` for every page; reach for two-pane when listing + viewing.

---

## 11. Constants you should not change without reason

| Thing | Value | Why |
|-------|-------|-----|
| Sidebar width | `220px` | Fits the longest nav label without truncation |
| Default button height | `h-8` (32px) | Matches Input height — clean form rows |
| Base radius | `0.25rem` (4px) | Quiet, dense, slightly modern |
| Page padding | `px-8 py-6` | Comfortable density on 13" laptops |
| Page max width | `max-w-6xl` (1280px) | Readable line lengths for tables and forms |
| Smallest text | `text-2xs` (11px) | Used by badges + metadata only |
| Body font | system stack | Zero network cost, native feel |
| Focus ring | 1px solid, 1px offset | Visible without being heavy |
| Scrollbar | 8px webkit | Slim but grippable |
