import { useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { closeDialogWithExitAnimation } from "@/lib/topLayer";

interface ModalDialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  maxWidthClass?: string;
  lightDismiss?: boolean;
  children: ReactNode;
}

export function ModalDialog({
  open,
  onClose,
  title,
  subtitle,
  maxWidthClass = "max-w-2xl",
  lightDismiss = false,
  children,
}: ModalDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const pressedOutsideRef = useRef(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (open) {
      dialog.removeAttribute("data-closing");
      if (!dialog.open) {
        dialog.showModal();
      }
    } else if (dialog.open) {
      void closeDialogWithExitAnimation(dialog);
    }
  }, [open]);

  const isOutsideDialog = (e: React.PointerEvent<HTMLDialogElement>, dialog: HTMLDialogElement) => {
    const r = dialog.getBoundingClientRect();
    return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
  };

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      className={`lt-modal w-full ${maxWidthClass} p-0 shadow-2xl open:flex open:flex-col`}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onPointerDown={(e) => {
        if (!lightDismiss || !dialogRef.current) return;
        pressedOutsideRef.current = isOutsideDialog(e, dialogRef.current);
      }}
      onPointerUp={(e) => {
        if (!lightDismiss || !dialogRef.current) return;
        if (pressedOutsideRef.current && isOutsideDialog(e, dialogRef.current)) {
          onClose();
        }
        pressedOutsideRef.current = false;
      }}
    >
      <div className="flex shrink-0 items-start justify-between gap-4 border-b border-border/70 px-6 py-4">
        <div>
          <h2 id={titleId} className="text-base font-bold text-[var(--md-sys-color-on-surface)]">
            {title}
          </h2>
          {subtitle && (
            <p className="mt-0.5 text-xs text-[var(--md-sys-color-on-surface-variant)]">
              {subtitle}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭弹窗"
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[var(--md-sys-color-on-surface-variant)] transition-colors hover:bg-[var(--md-sys-color-surface-container)] hover:text-[var(--md-sys-color-on-surface)]"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{open ? children : null}</div>
    </dialog>
  );
}
