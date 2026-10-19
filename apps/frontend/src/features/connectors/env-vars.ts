import { z } from "zod"

export const envRowSchema = z.object({
  key: z
    .string()
    .trim()
    .regex(/^[A-Za-z_][A-Za-z0-9_]{0,127}$/, "Letters, digits and '_', not starting with a digit."),
  value: z.string().max(4096),
})
export type EnvRow = z.infer<typeof envRowSchema>

export const toEnvRecord = (rows: EnvRow[]) => Object.fromEntries(rows.map((r) => [r.key, r.value]))
