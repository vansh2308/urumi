import { z } from "zod"

import type { CredentialInput } from "@/lib/api/types"

/** "keep" and "clear" only apply when editing a connector that already has a credential. */
export type CredentialMode = "none" | "keep" | "clear" | "bearer" | "header"

export const credentialSchema = z.object({
  credential_mode: z.enum(["none", "keep", "clear", "bearer", "header"]),
  credential_header: z.string().trim().max(64),
  credential_value: z.string().max(4096),
})
export type CredentialValues = z.infer<typeof credentialSchema>

/** Cross-field rules for the credential fields; call from the form schema's superRefine. */
export function refineCredential(v: CredentialValues, ctx: z.RefinementCtx) {
  if ((v.credential_mode === "bearer" || v.credential_mode === "header") && !v.credential_value.trim()) {
    ctx.addIssue({ code: "custom", path: ["credential_value"], message: "Enter the token or value." })
  }
  if (v.credential_mode === "header" && !/^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$/.test(v.credential_header)) {
    ctx.addIssue({ code: "custom", path: ["credential_header"], message: "Enter a header name, e.g. X-Api-Key." })
  }
}

export function toCredential(v: CredentialValues): CredentialInput | undefined {
  if (v.credential_mode === "bearer") return { type: "bearer", value: v.credential_value.trim() }
  if (v.credential_mode === "header")
    return { type: "header", header_name: v.credential_header, value: v.credential_value.trim() }
  return undefined
}

export function credentialDefaults(existing?: { header_name: string } | null): CredentialValues {
  return { credential_mode: existing ? "keep" : "none", credential_header: "", credential_value: "" }
}
