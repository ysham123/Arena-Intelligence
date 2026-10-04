import React, { useEffect, useRef } from "react";
import {
  ArrowRight,
  Check,
  Plus,
  Copy,
  Play,
  Pause,
  RotateCcw,
  Menu,
  House,
  Plug,
  Layers,
  BookOpen,
  Settings,
  Activity,
  X,
  ChevronRight,
  TriangleAlert,
  Columns2,
  Focus,
  FileCheck2,
} from "lucide-react";
export const icons = {
  arrow: ArrowRight,
  check: Check,
  plus: Plus,
  copy: Copy,
  play: Play,
  pause: Pause,
  restart: RotateCcw,
  menu: Menu,
  overview: House,
  connections: Plug,
  sessions: Layers,
  guide: BookOpen,
  settings: Settings,
  signal: Activity,
  close: X,
  chevron: ChevronRight,
  warning: TriangleAlert,
  compare: Columns2,
  focus: Focus,
  review: FileCheck2,
};
export function Button({
  children,
  onClick,
  primary = false,
  symbol,
  disabled = false,
  className = "",
  type = "button",
}: {
  children: React.ReactNode;
  onClick?: () => void;
  primary?: boolean;
  symbol?: keyof typeof icons;
  disabled?: boolean;
  className?: string;
  type?: "button" | "submit";
}) {
  const Icon = symbol ? icons[symbol] : null;
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={
        (primary ? "primary" : "secondary") +
        " " +
        (Icon ? "button-icon " : "") +
        className
      }
    >
      {Icon && <Icon className="icon" aria-hidden />}
      {children}
    </button>
  );
}
export function Status({ value = "unknown" }: { value?: string }) {
  return (
    <span className={"status " + value.replace(/[^a-z_]/g, "")}>
      {value.replaceAll("_", " ")}
    </span>
  );
}
export function PageHeading({
  title,
  description,
  eyebrow,
  children,
}: {
  title: string;
  description?: string;
  eyebrow: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {children && <div className="button-row">{children}</div>}
    </header>
  );
}
export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const previousFocus = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected)
        previousFocus.focus({ preventScroll: true });
    };
  }, []);
  return (
    <dialog ref={ref} className="dialog" aria-label={title} onCancel={onClose}>
      <div className="dialog-header">
        <h2>{title}</h2>
        <Button onClick={onClose} className="text-button">
          Close
        </Button>
      </div>
      <div className="dialog-body">{children}</div>
    </dialog>
  );
}
export function Empty({
  title,
  description,
  children,
  symbol = "sessions",
}: {
  title: string;
  description: string;
  children?: React.ReactNode;
  symbol?: keyof typeof icons;
}) {
  const Icon = icons[symbol];
  return (
    <section className="surface empty-state">
      <Icon className="icon" aria-hidden />
      <h2>{title}</h2>
      <p>{description}</p>
      {children}
    </section>
  );
}
export function date(value?: string) {
  if (!value) return "Not received";
  const result = new Date(value);
  return Number.isNaN(result.valueOf())
    ? value
    : result.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
}
export { numeric } from "./presentation";
