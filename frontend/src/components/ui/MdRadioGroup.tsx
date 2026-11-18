import { Check } from "lucide-react";

export interface MdRadioOption<T extends string> {
  value: T;
  label: string;
  description?: string;
  badge?: string;
}

interface MdRadioGroupProps<T extends string> {
  value: T;
  onChange: (value: T) => void;
  options: Array<MdRadioOption<T>>;
  ariaLabel?: string;
  columns?: 1 | 2 | 3;
  disabled?: boolean;
  className?: string;
}

export function MdRadioGroup<T extends string>({
  value,
  onChange,
  options,
  ariaLabel,
  columns = 1,
  disabled = false,
  className = "",
}: MdRadioGroupProps<T>) {
  const gridClass =
    columns === 3
      ? "grid-cols-1 sm:grid-cols-3"
      : columns === 2
        ? "grid-cols-1 sm:grid-cols-2"
        : "grid-cols-1";

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={`grid gap-2.5 ${gridClass} ${className}`}
    >
      {options.map((opt) => {
        const selected = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            onClick={() => onChange(opt.value)}
            className={`flex items-start gap-3 rounded-[var(--md-sys-shape-corner-md)] border px-3.5 py-3 text-left transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--md-sys-color-primary)] disabled:cursor-not-allowed disabled:opacity-55 ${
              selected
                ? "border-[var(--md-sys-color-primary)] bg-[var(--md-sys-color-primary-container)]/45 shadow-sm"
                : "border-[var(--md-sys-color-outline-variant)] bg-[var(--md-sys-color-surface-container-lowest)] hover:bg-[var(--md-sys-color-surface-container-low)]"
            }`}
          >
            <span
              aria-hidden="true"
              className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 transition-colors ${
                selected
                  ? "border-[var(--md-sys-color-primary)] bg-[var(--md-sys-color-primary)] text-[var(--md-sys-color-on-primary)]"
                  : "border-[var(--md-sys-color-outline)] bg-transparent"
              }`}
            >
              {selected && <Check className="h-2.5 w-2.5 stroke-[3]" />}
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold text-[var(--md-sys-color-on-surface)]">
                  {opt.label}
                </span>
                {opt.badge && (
                  <span className="rounded-full bg-[var(--md-sys-color-secondary-container)] px-2 py-0.5 text-[10px] font-semibold text-[var(--md-sys-color-on-secondary-container)]">
                    {opt.badge}
                  </span>
                )}
              </span>
              {opt.description && (
                <span className="mt-0.5 block text-xs text-[var(--md-sys-color-on-surface-variant)]">
                  {opt.description}
                </span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}
