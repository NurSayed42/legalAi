import { lazy, Suspense, type ReactNode } from "react";

export interface BottomSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
}

const loadImpl = () => import("./BottomSheetImpl");
const Impl = lazy(loadImpl);

/** framer-motion is only needed here, so it stays out of the first download. */
export const prefetchBottomSheet = () => void loadImpl().catch(() => {});

export function BottomSheet(props: BottomSheetProps) {
  return (
    <Suspense fallback={null}>
      <Impl {...props} />
    </Suspense>
  );
}
