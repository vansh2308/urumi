import type { ComponentProps, ReactNode } from "react"
import { Controller, type Control, type FieldPath, type FieldValues } from "react-hook-form"

import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"

/** A labelled react-hook-form input with accessible validation messages. */
export function TextField<T extends FieldValues>({
  control,
  name,
  label,
  description,
  ...inputProps
}: {
  control: Control<T>
  name: FieldPath<T>
  label: string
  description?: ReactNode
} & Omit<ComponentProps<typeof Input>, "name" | "defaultValue">) {
  return (
    <Controller
      control={control}
      name={name}
      render={({ field, fieldState }) => (
        <Field data-invalid={fieldState.invalid}>
          <FieldLabel htmlFor={name}>{label}</FieldLabel>
          <Input
            id={name}
            {...inputProps}
            {...field}
            value={field.value ?? ""}
            aria-invalid={fieldState.invalid}
          />
          {description && <FieldDescription>{description}</FieldDescription>}
          {fieldState.invalid && <FieldError errors={[fieldState.error]} />}
        </Field>
      )}
    />
  )
}
