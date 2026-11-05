import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Spinner } from "./Spinner";
import { Tooltip } from "./Tooltip";

const VARIANT = {
  primary: "bg-gold-500 text-on-gold shadow-1 hover:bg-gold-400 disabled:hover:bg-gold-500",
  secondary:
    "border border-line-2 bg-raised text-fg-1 hover:border-line-strong hover:bg-field disabled:hover:bg-raised",
  ghost: "text-fg-2 hover:bg-field hover:text-fg-1 disabled:hover:bg-transparent disabled:hover:text-fg-2",
  danger: "border border-danger bg-danger-tint text-danger hover:bg-danger hover:text-canvas",
} as const;

const SIZE = {
  sm: "hit-target h-9 gap-1.5 rounded px-3 text-14",
  md: "h-11 gap-2 rounded px-4 text-14",
  lg: "h-12 gap-2 rounded-card px-6 text-16",
} as const;

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof VARIANT;
  size?: keyof typeof SIZE;
  loading?: boolean;
  icon?: ReactNode;
  align?: "center" | "start";
}

/** While loading, the label stays in the layout (hidden), so the button keeps its width. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    loading = false,
    icon,
    align = "center",
    className,
    children,
    disabled,
    type = "button",
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "press relative inline-flex select-none items-center whitespace-nowrap font-medium disabled:opacity-50",
        align === "start" ? "justify-start" : "justify-center",
        VARIANT[variant],
        SIZE[size],
        className,
      )}
      {...rest}
    >
      <span className={cn("inline-flex items-center gap-2", loading && "invisible")}>
        {icon}
        {children}
      </span>
      {loading && (
        <span className="absolute inset-0 flex items-center justify-center">
          <Spinner />
        </span>
      )}
    </button>
  );
});

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  size?: "sm" | "md";
  tone?: "ghost" | "secondary" | "primary";
  showTooltip?: boolean;
}

const ICON_TONE = {
  ghost: "text-fg-2 hover:bg-field hover:text-fg-1",
  secondary: "border border-line-2 bg-raised text-fg-1 hover:border-line-strong hover:bg-field",
  primary: "bg-gold-500 text-on-gold hover:bg-gold-400 shadow-1",
} as const;

/** Icon-only buttons always carry a label for screen readers. The small size keeps a 44px hit area. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, size = "md", tone = "ghost", showTooltip = true, className, children, type = "button", ...rest },
  ref,
) {
  const button = (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      className={cn(
        "press relative inline-flex shrink-0 items-center justify-center rounded disabled:opacity-50",
        size === "md" ? "h-11 w-11" : "hit-target h-9 w-9",
        ICON_TONE[tone],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
  return showTooltip ? <Tooltip content={label}>{button}</Tooltip> : button;
});
