import { FileText, Upload, X } from "lucide-react";
import { useRef, useState } from "react";

interface MdFileDropzoneProps {
  files: File[];
  onFilesChange: (files: File[]) => void;
  multiple?: boolean;
  accept?: string;
  label?: string;
  hint?: string;
  disabled?: boolean;
  ariaLabel?: string;
}

export function MdFileDropzone({
  files,
  onFilesChange,
  multiple = false,
  accept,
  label = "点击或拖拽文件到此区域上传",
  hint,
  disabled = false,
  ariaLabel,
}: MdFileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const acceptFiles = (incoming: FileList | null) => {
    if (!incoming || incoming.length === 0) return;
    const picked = Array.from(incoming);
    if (multiple) {
      const merged = [...files];
      for (const f of picked) {
        if (!merged.some((m) => m.name === f.name && m.size === f.size)) {
          merged.push(f);
        }
      }
      onFilesChange(merged);
    } else {
      onFilesChange([picked[0]]);
    }
  };

  return (
    <div className="space-y-2">
      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-label={ariaLabel ?? label}
        aria-disabled={disabled}
        onClick={() => {
          if (!disabled) inputRef.current?.click();
        }}
        onKeyDown={(e) => {
          if (!disabled && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (!disabled) acceptFiles(e.dataTransfer.files);
        }}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-[var(--md-sys-shape-corner-md)] border-2 border-dashed px-4 py-5 text-center transition-colors ${
          disabled
            ? "cursor-not-allowed border-[var(--md-sys-color-outline-variant)] bg-[var(--md-sys-color-surface-container-low)] opacity-60"
            : dragging
              ? "border-[var(--md-sys-color-primary)] bg-primary-container/40"
              : "border-[var(--md-sys-color-outline-variant)] bg-surface-low/70 hover:border-primary/60 hover:bg-surface-container/60"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          multiple={multiple}
          accept={accept}
          disabled={disabled}
          className="sr-only"
          onChange={(e) => {
            acceptFiles(e.target.files);
            e.currentTarget.value = "";
          }}
        />
        <span className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-[var(--md-sys-color-primary-container)] text-[var(--md-sys-color-on-primary-container)]">
          <Upload className="h-4 w-4" aria-hidden="true" />
        </span>
        <span className="mt-2 text-xs font-semibold text-[var(--md-sys-color-on-surface)]">
          {label}
        </span>
        {hint && (
          <span className="mt-0.5 text-[11px] text-[var(--md-sys-color-on-surface-variant)]">
            {hint}
          </span>
        )}
      </div>

      {files.length > 0 && (
        <ul className="space-y-1.5">
          {files.map((file, idx) => (
            <li
              key={`${file.name}-${file.size}-${idx}`}
              className="flex items-center justify-between gap-2 rounded-[var(--md-sys-shape-corner-sm)] border border-[var(--md-sys-color-outline-variant)] bg-[var(--md-sys-color-surface-container-lowest)] px-3 py-1.5 text-xs"
            >
              <span className="flex min-w-0 items-center gap-2 text-[var(--md-sys-color-on-surface)]">
                <FileText className="h-3.5 w-3.5 shrink-0 text-[var(--md-sys-color-primary)]" />
                <span className="truncate font-medium">{file.name}</span>
                <span className="shrink-0 text-[11px] text-[var(--md-sys-color-on-surface-variant)]">
                  ({(file.size / 1024).toFixed(1)} KB)
                </span>
              </span>
              <button
                type="button"
                aria-label={`移除文件 ${file.name}`}
                onClick={() => onFilesChange(files.filter((_, i) => i !== idx))}
                className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[var(--md-sys-color-on-surface-variant)] hover:bg-[var(--md-sys-color-surface-container)] hover:text-[var(--md-sys-color-error)]"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
