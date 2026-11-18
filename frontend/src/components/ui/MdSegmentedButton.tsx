import { Check } from "lucide-react";
import type { ReactNode } from "react";

export interface MdSegmentOption<T extends string> {
  value: T;
  label: string;
  count?: number;
  icon?: ReactNode;
}

interface MdSegmentedButtonProps<T extends string> {
  value: T;
  onChange: (value: T) => void;
  options: Array<MdSegmentOption<T>>;
  ariaLabel?: string;
  showCheckIcon?: boolean;
  fullWidth?: boolean;
  className?: string;
}

export function MdSegmentedButton<T extends string>({
  value,
  onChange,
  options,
  ariaLabel,
  showCheckIcon = false,
  fullWidth = false,
  className = "",
}: MdSegmentedButtonProps<T>) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={`lt-segmented ${fullWidth ? "w-full justify-between" : ""} ${className}`}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.value)}
            className={`inline-flex items-center justify-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-semibold transition-all ${
              fullWidth ? "flex-1" : ""
            } ${
              active
                ? "bg-[var(--md-sys-color-secondary-container)] text-[var(--md-sys-color-on-secondary-container)] shadow-sm"
                : "text-[var(--md-sys-color-on-surface-variant)] hover:bg-[var(--md-sys-color-surface-container-low)] hover:text-[var(--md-sys-color-on-surface)]"
            }`}
          >
            {active && showCheckIcon ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : opt.icon}
            <span>{opt.label}</span>
            {opt.count !== undefined && (
              <span
                className={`rounded-full px-1.5 py-0.5 text-[10px] font-bold leading-none ${
                  active
                    ? "bg-[var(--md-sys-color-surface-container-lowest)]/80 text-[var(--md-sys-color-on-secondary-container)]"
                    : "bg-[var(--md-sys-color-surface-container-high)] text-[var(--md-sys-color-on-surface-variant)]"
                }`}
              >
                {opt.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
