import { PlusIcon, Trash2Icon } from "lucide-react"
import { useFieldArray, type Control, type FieldValues, type Path, type ArrayPath } from "react-hook-form"

import { TextField } from "@/components/common/form-fields"
import { Button } from "@/components/ui/button"
import { FieldDescription, FieldLegend, FieldSet } from "@/components/ui/field"

/** Editable list of environment variables; `secret` masks the values while typing. */
export function KeyValueFields<T extends FieldValues>({
  control,
  name,
  legend,
  description,
  secret = false,
}: {
  control: Control<T>
  name: ArrayPath<T>
  legend: string
  description?: string
  secret?: boolean
}) {
  const { fields, append, remove } = useFieldArray({ control, name })
  return (
    <FieldSet>
      <FieldLegend variant="label">{legend}</FieldLegend>
      {description && <FieldDescription>{description}</FieldDescription>}
      {fields.map((f, i) => (
        <div key={f.id} className="grid grid-cols-[1fr_1fr_auto] items-start gap-2">
          <TextField
            control={control}
            name={`${name}.${i}.key` as Path<T>}
            label="Name"
            placeholder="NAME"
            className="font-mono"
            autoComplete="off"
          />
          <TextField
            control={control}
            name={`${name}.${i}.value` as Path<T>}
            label="Value"
            type={secret ? "password" : "text"}
            autoComplete={secret ? "new-password" : "off"}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="mt-7"
            aria-label={`Remove ${legend.toLowerCase()} ${i + 1}`}
            onClick={() => remove(i)}
          >
            <Trash2Icon />
          </Button>
        </div>
      ))}
      <div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => append({ key: "", value: "" } as never)}
        >
          <PlusIcon /> Add variable
        </Button>
      </div>
    </FieldSet>
  )
}
