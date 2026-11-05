import * as Primitive from "@radix-ui/react-dialog";
import { AnimatePresence, motion, useDragControls, useReducedMotion, type PanInfo } from "framer-motion";
import { X } from "lucide-react";
import type { BottomSheetProps } from "./BottomSheet";
import { IconButton } from "./Button";

const SPRING = { type: "spring", stiffness: 420, damping: 40, mass: 0.9 } as const;
const CLOSE_AFTER_PX = 96;
const CLOSE_AT_VELOCITY = 600;

/** Radix supplies the focus trap, Escape and screen-reader semantics. framer-motion supplies the spring and the swipe. */
export default function BottomSheetImpl({ open, onOpenChange, title, children, footer }: BottomSheetProps) {
  const reduced = useReducedMotion();
  const controls = useDragControls();
  const onDragEnd = (_: unknown, info: PanInfo) => {
    if (info.offset.y > CLOSE_AFTER_PX || info.velocity.y > CLOSE_AT_VELOCITY) onOpenChange(false);
  };
  const hidden = reduced ? { opacity: 0 } : { y: "100%" };
  const shown = reduced ? { opacity: 1 } : { y: 0 };

  return (
    <Primitive.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <Primitive.Portal forceMount>
            <Primitive.Overlay asChild forceMount>
              <motion.div
                className="fixed inset-0 z-sheet bg-scrim"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.18 }}
              />
            </Primitive.Overlay>
            <Primitive.Content asChild forceMount aria-describedby={undefined}>
              <motion.div
                className="fixed inset-x-0 bottom-0 z-sheet flex max-h-[85dvh] flex-col rounded-t-panel border-t border-line-2 bg-raised shadow-3"
                initial={hidden}
                animate={shown}
                exit={hidden}
                transition={reduced ? { duration: 0.18 } : SPRING}
                drag={reduced ? false : "y"}
                dragListener={false}
                dragControls={controls}
                dragConstraints={{ top: 0, bottom: 0 }}
                dragElastic={{ top: 0, bottom: 0.7 }}
                onDragEnd={onDragEnd}
              >
                {/* The handle is decorative for screen readers: they close with Escape or the close button. */}
                <div
                  className="flex shrink-0 touch-none cursor-grab justify-center pb-1 pt-2"
                  aria-hidden="true"
                  onPointerDown={(e) => controls.start(e)}
                >
                  <span className="h-1 w-10 rounded-full bg-line-2" />
                </div>
                <div className="flex shrink-0 items-center justify-between gap-2 px-4 pb-2">
                  <Primitive.Title className="text-18 font-semibold text-fg-1">{title}</Primitive.Title>
                  <Primitive.Close asChild>
                    <IconButton label="বন্ধ করুন" showTooltip={false}>
                      <X size={18} strokeWidth={1.75} aria-hidden="true" />
                    </IconButton>
                  </Primitive.Close>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-4">{children}</div>
                {footer && (
                  <div className="flex shrink-0 gap-2 border-t border-line-1 px-4 pb-[calc(12px+env(safe-area-inset-bottom,0px))] pt-3">
                    {footer}
                  </div>
                )}
              </motion.div>
            </Primitive.Content>
          </Primitive.Portal>
        )}
      </AnimatePresence>
    </Primitive.Root>
  );
}
