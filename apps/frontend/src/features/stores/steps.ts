import type { Store } from "@/lib/api/types"

/** Provisioning steps in order, with what the dashboard shows while the next one runs. */
export const STORE_STEPS = [
  { id: "namespace", label: "Namespace" },
  { id: "secrets", label: "Secrets" },
  { id: "mcp-db", label: "MCP database" },
  { id: "release", label: "Helm release" },
  { id: "wait-ready", label: "Pods ready" },
  { id: "setup", label: "WooCommerce setup" },
  { id: "register", label: "MCP server registered" },
] as const

/** The step currently running (the one after the last finished), for a provisioning store. */
export function currentStep(store: Pick<Store, "status" | "step">): string | null {
  if (store.status !== "provisioning" && store.status !== "requested") return null
  if (store.status === "requested" || !store.step) return STORE_STEPS[0].label
  const index = STORE_STEPS.findIndex((s) => s.id === store.step)
  return STORE_STEPS[Math.min(index + 1, STORE_STEPS.length - 1)].label
}
