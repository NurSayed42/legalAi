import * as Primitive from "@radix-ui/react-tooltip";
import type { ReactElement, ReactNode } from "react";

export const TooltipProvider = Primitive.Provider;

export function Tooltip({ content, children }: { content: ReactNode; children: ReactElement }) {
  return (
    <Primitive.Root>
      <Primitive.Trigger asChild>{children}</Primitive.Trigger>
      <Primitive.Portal>
        <Primitive.Content
          sideOffset={6}
          collisionPadding={8}
          className="popover-in z-toast max-w-[240px] rounded border border-line-2 bg-field px-2 py-1 text-14 leading-snug text-fg-1 shadow-2"
        >
          {content}
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}
