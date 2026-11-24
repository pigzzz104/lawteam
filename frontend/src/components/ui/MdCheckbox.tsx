import { Check, Minus } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

interface MdCheckboxProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: ReactNode;
  description?: string;
  indeterminate?: boolean;
  disabled?: boolean;
  className?: string;
  ariaLabel?: string;
}

export function MdCheckbox({
  checked,
  onChange,
  label,
  description,
  indeterminate = false,
  disabled = false,
  className = "",
  ariaLabel,
}: MdCheckboxProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const active = checked || indeterminate;

  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.indeterminate = Boolean(indeterminate);
    }
  }, [indeterminate]);

  return (
    <label
      className={`inline-flex cursor-pointer select-none items-start gap-2.5 ${
        disabled ? "cursor-not-allowed opacity-55" : ""
      } ${className}`}
    >
      <span className="relative mt-0.5 inline-flex h-[18px] w-[18px] shrink-0 items-center justify-center">
        <input
          ref={inputRef}
          type="checkbox"
          checked={checked}
          disabled={disabled}
          aria-label={ariaLabel}
          onChange={(e) => onChange(e.target.checked)}
          className="peer sr-only"
        />
        <span
          aria-hidden="true"
          className={`flex h-[18px] w-[18px] items-center justify-center rounded-[5px] border-2 transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-primary/40 ${
            active
              ? "border-[var(--md-sys-color-primary)] bg-[var(--md-sys-color-primary)] text-[var(--md-sys-color-on-primary)]"
              : "border-[var(--md-sys-color-outline)] bg-[var(--md-sys-color-surface-container-lowest)] text-transparent"
          }`}
        >
          {indeterminate ? (
            <Minus className="h-3.5 w-3.5 stroke-[2.6]" />
          ) : checked ? (
            <Check className="h-3.5 w-3.5 stroke-[2.6]" />
          ) : null}
        </span>
      </span>
      {(label || description) && (
        <span className="min-w-0 flex-1">
          {label && (
            <span className="block text-sm text-[var(--md-sys-color-on-surface)]">{label}</span>
          )}
          {description && (
            <span className="mt-0.5 block text-xs text-[var(--md-sys-color-on-surface-variant)]">
              {description}
            </span>
          )}
        </span>
      )}
    </label>
  );
}
