import { Check } from "lucide-react";

export const CALENDAR_PALETTE = [
  { hex: "#1b56b3", name: "钴蓝" },
  { hex: "#0b6b5c", name: "松绿" },
  { hex: "#6d28d9", name: "绛紫" },
  { hex: "#b45309", name: "琥珀" },
  { hex: "#b3261e", name: "朱红" },
  { hex: "#0369a1", name: "海蓝" },
  { hex: "#0f766e", name: "青瓷" },
  { hex: "#374151", name: "玄铁" },
] as const;

function getContrastInk(hex: string): string {
  const clean = hex.replace("#", "");
  if (clean.length !== 6) return "#ffffff";
  const r = Number.parseInt(clean.slice(0, 2), 16);
  const g = Number.parseInt(clean.slice(2, 4), 16);
  const b = Number.parseInt(clean.slice(4, 6), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return lum > 0.62 ? "#181c23" : "#ffffff";
}

interface MdColorSwatchesProps {
  value: string;
  onChange: (hex: string) => void;
  ariaLabel?: string;
}

export function MdColorSwatches({
  value,
  onChange,
  ariaLabel = "选择日历标识色",
}: MdColorSwatchesProps) {
  const normalized = (value || "#1b56b3").trim().toLowerCase();
  const inPalette = CALENDAR_PALETTE.some((c) => c.hex.toLowerCase() === normalized);
  const swatches = inPalette
    ? CALENDAR_PALETTE
    : [...CALENDAR_PALETTE, { hex: normalized, name: "当前颜色" }];

  return (
    <div role="radiogroup" aria-label={ariaLabel} className="flex flex-wrap items-center gap-2.5">
      {swatches.map((swatch) => {
        const selected = swatch.hex.toLowerCase() === normalized;
        return (
          <button
            key={swatch.hex}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={`${swatch.name} (${swatch.hex})`}
            title={swatch.name}
            onClick={() => onChange(swatch.hex)}
            style={{ backgroundColor: swatch.hex, color: getContrastInk(swatch.hex) }}
            className={`inline-flex h-8 w-8 items-center justify-center rounded-full border-2 transition-transform hover:scale-105 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--md-sys-color-primary)] ${
              selected
                ? "border-[var(--md-sys-color-on-surface)] ring-2 ring-[var(--md-sys-color-primary)]/35 scale-105"
                : "border-white/80 shadow-sm"
            }`}
          >
            {selected && <Check className="h-4 w-4 stroke-[2.8]" aria-hidden="true" />}
          </button>
        );
      })}
    </div>
  );
}
