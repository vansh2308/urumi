import { describe, expect, it } from "vitest"

import { isStoreServer } from "@/features/connectors/access"

import { currentStep } from "./steps"

describe("currentStep", () => {
  it("shows the step after the last finished one while provisioning", () => {
    expect(currentStep({ status: "requested", step: null })).toBe("Namespace")
    expect(currentStep({ status: "provisioning", step: "release" })).toBe("Pods ready")
    expect(currentStep({ status: "provisioning", step: "setup" })).toBe("MCP server registered")
  })

  it("is empty once the store settled", () => {
    expect(currentStep({ status: "ready", step: "register" })).toBeNull()
    expect(currentStep({ status: "failed", step: "release" })).toBeNull()
  })
})

describe("isStoreServer", () => {
  it("recognises store connectors from either listing", () => {
    expect(isStoreServer({ registry: { kind: "store" }, team: null })).toBe(true)
    expect(isStoreServer({ registry: null, team: { kind: "store" } })).toBe(true)
    expect(isStoreServer({ registry: { kind: "hosted" }, team: { kind: "remote" } })).toBe(false)
  })
})
