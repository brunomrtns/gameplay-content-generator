import { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("card-premium p-6", className)}>{children}</div>
  );
}

export function Button({
  children,
  onClick,
  variant = "default",
  size = "default",
  className,
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "ghost" | "outline" | "danger";
  size?: "default" | "sm" | "lg" | "icon";
  className?: string;
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  const variants = {
    default: "bg-surface-elevated border border-border text-text hover:border-border-bright",
    primary: "bg-accent text-white hover:bg-accent-hover glow-accent",
    ghost: "text-text-secondary hover:text-text hover:bg-surface-hover",
    outline: "border border-border text-text hover:border-accent hover:text-accent",
    danger: "bg-red-600/10 border border-red-600/30 text-red-400 hover:bg-red-600/20",
  };
  const sizes = {
    default: "h-10 px-5 text-sm",
    sm: "h-8 px-3.5 text-xs",
    lg: "h-12 px-8 text-base",
    icon: "h-10 w-10",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-xl font-medium transition-all duration-300",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        variants[variant],
        sizes[size],
        className
      )}
    >
      {children}
    </button>
  );
}

export function Input({
  value,
  onChange,
  placeholder,
  type = "text",
  className,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      className={cn(
        "h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text",
        "placeholder:text-text-muted backdrop-blur-sm transition-all duration-300",
        "disabled:opacity-50",
        className
      )}
    />
  );
}

export function Label({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <label className={cn("text-sm font-medium tracking-tight text-text-secondary mb-1.5 block", className)}>
      {children}
    </label>
  );
}

export function Badge({
  children,
  variant = "default",
  className,
}: {
  children: ReactNode;
  variant?: "default" | "success" | "warning" | "error" | "info";
  className?: string;
}) {
  const variants = {
    default: "bg-surface-elevated text-text-secondary border-border",
    success: "bg-accent/10 text-accent border-accent/30",
    warning: "bg-accent-warm/10 text-accent-warm border-accent-warm/30",
    error: "bg-red-600/10 text-red-400 border-red-600/30",
    info: "bg-blue-500/10 text-blue-400 border-blue-500/30",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        variants[variant],
        className
      )}
    >
      {children}
    </span>
  );
}

export function Select({
  value,
  onChange,
  children,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        "h-11 w-full rounded-xl border border-border bg-bg/60 px-4 text-sm text-text",
        "backdrop-blur-sm transition-all duration-300 cursor-pointer",
        className
      )}
    >
      {children}
    </select>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <div className={cn("animate-spin rounded-full border-2 border-border border-t-accent h-5 w-5", className)} />
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      {icon && <div className="mb-4 text-text-muted">{icon}</div>}
      <h3 className="text-lg font-medium text-text mb-1">{title}</h3>
      {description && <p className="text-sm text-text-muted max-w-sm">{description}</p>}
      {action && <div className="mt-6">{action}</div>}
    </div>
  );
}
