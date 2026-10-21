import { StatusBadge } from "@/components/common/status-badge"
import { Spinner } from "@/components/ui/spinner"
import type { Store } from "@/lib/api/types"

import { currentStep } from "./steps"

const BADGE: Record<Store["phase"], string> = {
  Provisioning: "pending",
  Ready: "ready",
  Failed: "failed",
  Deleting: "stopped",
  Deleted: "disabled",
}

export function StorePhaseBadge({ store }: { store: Store }) {
  const step = currentStep(store)
  return (
    <span className="inline-flex items-center gap-2">
      <StatusBadge status={BADGE[store.phase]} label={store.phase} />
      {(store.phase === "Provisioning" || store.phase === "Deleting") && (
        <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
          <Spinner className="size-3" />
          {step ?? "Removing resources"}
        </span>
      )}
    </span>
  )
}
