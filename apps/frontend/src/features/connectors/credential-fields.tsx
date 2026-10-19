import { Controller, type Control, type FieldValues, type Path, useWatch } from "react-hook-form"

import { TextField } from "@/components/common/form-fields"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { type CredentialMode, type CredentialValues } from "@/features/connectors/credential"

/** Credential picker: none, bearer token or custom header (plus keep/remove when editing). The
 *  value is write-only: the gateway encrypts it and never sends it back. */
export function CredentialFields<T extends FieldValues & CredentialValues>({
  control,
  existing,
}: {
  control: Control<T>
  existing?: { header_name: string } | null
}) {
  const mode = useWatch({ control, name: "credential_mode" as Path<T> }) as CredentialMode
  return (
    <>
      <Controller
        control={control}
        name={"credential_mode" as Path<T>}
        render={({ field }) => (
          <Field>
            <FieldLabel htmlFor="credential-mode">Credential</FieldLabel>
            <Select value={field.value} onValueChange={field.onChange}>
              <SelectTrigger id="credential-mode" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {existing ? (
                  <>
                    <SelectItem value="keep">Keep current ({existing.header_name})</SelectItem>
                    <SelectItem value="clear">Remove credential</SelectItem>
                  </>
                ) : (
                  <SelectItem value="none">None</SelectItem>
                )}
                <SelectItem value="bearer">{existing ? "Replace with bearer token" : "Bearer token"}</SelectItem>
                <SelectItem value="header">{existing ? "Replace with custom header" : "Custom header"}</SelectItem>
              </SelectContent>
            </Select>
            <FieldDescription>
              Sent by the gateway on every call to this server. Stored encrypted; never shown again or
              sent to clients.
            </FieldDescription>
          </Field>
        )}
      />
      {mode === "header" && (
        <TextField
          control={control}
          name={"credential_header" as Path<T>}
          label="Header name"
          placeholder="X-Api-Key"
          autoComplete="off"
        />
      )}
      {(mode === "bearer" || mode === "header") && (
        <TextField
          control={control}
          name={"credential_value" as Path<T>}
          label={mode === "bearer" ? "Token" : "Header value"}
          type="password"
          autoComplete="new-password"
          placeholder={mode === "bearer" ? "Sent as Authorization: Bearer <token>" : undefined}
        />
      )}
    </>
  )
}
