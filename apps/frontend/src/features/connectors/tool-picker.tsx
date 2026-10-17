import { Checkbox } from "@/components/ui/checkbox"
import { Field, FieldLabel } from "@/components/ui/field"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Textarea } from "@/components/ui/textarea"
import type { ToolSet, ToolSummary } from "@/lib/api/types"

/** Choose "all tools" or a subset, from a known tool list or (if unknown) by typing names. */
export function ToolPicker({
  value,
  onChange,
  tools,
  allLabel = "All tools",
}: {
  value: ToolSet
  onChange: (value: ToolSet) => void
  tools: ToolSummary[] | null
  allLabel?: string
}) {
  const toggle = (name: string, checked: boolean) =>
    onChange({
      allow_all_tools: false,
      tools: checked ? [...new Set([...value.tools, name])] : value.tools.filter((t) => t !== name),
    })
  return (
    <div className="space-y-3">
      <RadioGroup
        value={value.allow_all_tools ? "all" : "some"}
        onValueChange={(v) => onChange({ ...value, allow_all_tools: v === "all" })}
      >
        <div className="flex items-center gap-2">
          <RadioGroupItem value="some" id="tools-some" />
          <Label htmlFor="tools-some">Only selected tools</Label>
        </div>
        <div className="flex items-center gap-2">
          <RadioGroupItem value="all" id="tools-all" />
          <Label htmlFor="tools-all">{allLabel}</Label>
        </div>
      </RadioGroup>
      {!value.allow_all_tools &&
        (tools ? (
          <ScrollArea className="h-56 rounded-md border">
            <div className="space-y-1 p-2">
              {tools.map((t) => (
                <label
                  key={t.name}
                  className="flex cursor-pointer items-start gap-2 rounded-md p-2 hover:bg-muted/50"
                >
                  <Checkbox
                    checked={value.tools.includes(t.name)}
                    onCheckedChange={(c) => toggle(t.name, Boolean(c))}
                    className="mt-0.5"
                  />
                  <span className="min-w-0">
                    <span className="block font-mono text-xs">{t.name}</span>
                    {t.description && (
                      <span className="line-clamp-2 text-xs text-muted-foreground">{t.description}</span>
                    )}
                  </span>
                </label>
              ))}
              {!tools.length && (
                <p className="p-2 text-sm text-muted-foreground">No tools to choose from.</p>
              )}
            </div>
          </ScrollArea>
        ) : (
          <Field>
            <FieldLabel htmlFor="tool-names">Tool names (one per line)</FieldLabel>
            <Textarea
              id="tool-names"
              rows={5}
              className="font-mono text-xs"
              value={value.tools.join("\n")}
              onChange={(e) =>
                onChange({
                  allow_all_tools: false,
                  tools: e.target.value
                    .split(/[\n,]/)
                    .map((s) => s.trim())
                    .filter(Boolean),
                })
              }
            />
          </Field>
        ))}
    </div>
  )
}
