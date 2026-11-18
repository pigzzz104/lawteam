import { AlertTriangle } from "lucide-react";
import { ModalDialog } from "@/components/ModalDialog";

interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "primary" | "danger";
  loading?: boolean;
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel = "确认执行",
  cancelLabel = "取消",
  tone = "primary",
  loading = false,
}: ConfirmDialogProps) {
  return (
    <ModalDialog
      open={open}
      onClose={onClose}
      title={title}
      maxWidthClass="max-w-md"
    >
      <div className="space-y-4">
        <div className="flex items-start gap-3">
          <span
            className={`mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
              tone === "danger"
                ? "bg-[var(--md-sys-color-error-container)] text-[var(--md-sys-color-on-error-container)]"
                : "bg-[var(--md-sys-color-primary-container)] text-[var(--md-sys-color-on-primary-container)]"
            }`}
          >
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
          </span>
          <p className="text-sm leading-relaxed text-[var(--md-sys-color-on-surface-variant)]">
            {description}
          </p>
        </div>

        <div className="flex items-center justify-end gap-2 pt-2">
          <button
            type="button"
            disabled={loading}
            onClick={onClose}
            className="lt-btn-secondary"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={onConfirm}
            className={tone === "danger" ? "lt-btn-danger" : "lt-btn-primary"}
          >
            {loading ? "处理中..." : confirmLabel}
          </button>
        </div>
      </div>
    </ModalDialog>
  );
}
