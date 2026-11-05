import * as Primitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { Button, IconButton } from "./Button";

export function Dialog({
  open,
  onOpenChange,
  title,
  children,
  footer,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <Primitive.Root open={open} onOpenChange={onOpenChange}>
      <Primitive.Portal>
        <Primitive.Overlay className="scrim-in fixed inset-0 z-sheet bg-scrim" />
        <Primitive.Content
          aria-describedby={undefined}
          className="dialog-in fixed left-1/2 top-1/2 z-sheet max-h-[85dvh] w-[min(480px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-panel border border-line-2 bg-raised p-5 shadow-3"
        >
          <div className="mb-3 flex items-start justify-between gap-3">
            <Primitive.Title className="pt-2 text-18 font-semibold text-fg-1">{title}</Primitive.Title>
            <Primitive.Close asChild>
              <IconButton label="বন্ধ করুন" showTooltip={false}>
                <X size={18} strokeWidth={1.75} aria-hidden="true" />
              </IconButton>
            </Primitive.Close>
          </div>
          <div className="text-14 text-fg-2">{children}</div>
          {footer && <div className="mt-5 flex flex-wrap justify-end gap-2">{footer}</div>}
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  body,
  confirmLabel,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>থাক</Button>
          <Button
            variant="danger"
            onClick={() => {
              onConfirm();
              onOpenChange(false);
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      {body}
    </Dialog>
  );
}
