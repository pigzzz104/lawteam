import type { ReactNode } from "react";

interface MdSwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: ReactNode;
  description?: string;
  disabled?: boolean;
  id?: string;
  ariaLabel?: string;
  className?: string;
}

export function MdSwitch({
  checked,
  onChange,
  label,
  description,
  disabled = false,
  id,
  ariaLabel,
  className = "",
}: MdSwitchProps) {
  const toggleButton = (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => {
        if (!disabled) onChange(!checked);
      }}
      className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full border-2 transition-colors duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--md-sys-color-primary)] disabled:cursor-not-allowed disabled:opacity-50 ${
        checked
          ? "border-[var(--md-sys-color-primary)] bg-[var(--md-sys-color-primary)]"
          : "border-[var(--md-sys-color-outline)] bg-[var(--md-sys-color-surface-container-highest)]"
      }`}
    >
      <span
        aria-hidden="true"
        className={`pointer-events-none inline-block rounded-full transition-all duration-200 ${
          checked
            ? "h-5 w-5 translate-x-[22px] bg-[var(--md-sys-color-on-primary)] shadow-sm"
            : "h-4 w-4 translate-x-1 bg-[var(--md-sys-color-outline)]"
        }`}
      />
    </button>
  );

  if (!label && !description) {
    return <span className={`inline-flex items-center ${className}`}>{toggleButton}</span>;
  }

  return (
    <div
      className={`flex items-center justify-between gap-3 ${className}`}
      onClick={() => {
        if (!disabled) onChange(!checked);
      }}
    >
      <div className="min-w-0 flex-1 cursor-pointer select-none">
        {label && (
          <div className="text-sm font-medium text-[var(--md-sys-color-on-surface)]">{label}</div>
        )}
        {description && (
          <div className="mt-0.5 text-xs text-[var(--md-sys-color-on-surface-variant)]">
            {description}
          </div>
        )}
      </div>
      <div onClick={(e) => e.stopPropagation()}>{toggleButton}</div>
    </div>
  );
}
