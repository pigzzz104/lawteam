import { ChevronDown } from "lucide-react";
import type { SelectHTMLAttributes } from "react";

interface MdSelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  wrapperClassName?: string;
}

export function MdSelect({
  wrapperClassName = "",
  className = "",
  children,
  ...props
}: MdSelectProps) {
  return (
    <div className={`relative ${wrapperClassName}`}>
      <select
        {...props}
        className={`lt-input appearance-none pr-9 ${className}`}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--md-sys-color-on-surface-variant)]"
      />
    </div>
  );
}
