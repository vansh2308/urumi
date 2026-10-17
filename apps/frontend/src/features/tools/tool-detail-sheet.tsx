import { Link } from "react-router"

import { CopyButton } from "@/components/common/copy-button"
import { JsonView } from "@/components/common/json-view"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type { McpTool } from "@/lib/api/types"

import { RunToolForm } from "./run-tool-form"
import { paramsOf, riskOf } from "./schema"
import { ToolRiskBadge } from "./tool-risk-badge"

export function ToolDetailSheet({
  tool,
  teamId,
  connector,
  onOpenChange,
}: {
  tool: McpTool | null
  teamId: string
  connector: { slug: string; display_name: string }
  onOpenChange: (open: boolean) => void
}) {
  const params = paramsOf(tool?.inputSchema)
  return (
    <Sheet open={tool !== null} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 sm:max-w-xl">
        {tool && (
          <>
            <SheetHeader className="border-b">
              <SheetTitle className="flex items-center gap-2 font-mono">
                {tool.name}
                <CopyButton value={tool.name} label="Copy tool name" />
              </SheetTitle>
              <SheetDescription asChild>
                <div className="flex flex-wrap items-center gap-2">
                  <Link to={`/mcp-servers/${connector.slug}`} className="hover:underline">
                    {connector.display_name}
                  </Link>
                  <ToolRiskBadge risk={riskOf(tool)} />
                  {tool.annotations?.idempotentHint && <Badge variant="outline">Idempotent</Badge>}
                </div>
              </SheetDescription>
            </SheetHeader>
            <ScrollArea className="min-h-0 flex-1">
              <div className="space-y-5 p-4">
                {tool.description && (
                  <p className="text-sm whitespace-pre-wrap text-muted-foreground">{tool.description}</p>
                )}
                <Tabs defaultValue="params">
                  <TabsList>
                    <TabsTrigger value="params">Parameters</TabsTrigger>
                    <TabsTrigger value="schema">Input schema</TabsTrigger>
                    {tool.outputSchema && <TabsTrigger value="output">Output schema</TabsTrigger>}
                    <TabsTrigger value="run">Try it</TabsTrigger>
                  </TabsList>
                  <TabsContent value="params" className="pt-2">
                    {params.length ? (
                      <div className="overflow-hidden rounded-lg border">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Name</TableHead>
                              <TableHead>Type</TableHead>
                              <TableHead>Description</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {params.map((p) => (
                              <TableRow key={p.name}>
                                <TableCell className="align-top font-mono text-xs">
                                  {p.name}
                                  {p.required && <span className="text-destructive">*</span>}
                                </TableCell>
                                <TableCell className="align-top font-mono text-xs">{p.type}</TableCell>
                                <TableCell className="text-xs whitespace-normal">
                                  {p.description}
                                  {p.detail && <div className="text-muted-foreground">{p.detail}</div>}
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground">This tool takes no arguments.</p>
                    )}
                  </TabsContent>
                  <TabsContent value="schema" className="pt-2">
                    <JsonView value={tool.inputSchema ?? {}} />
                  </TabsContent>
                  {tool.outputSchema && (
                    <TabsContent value="output" className="pt-2">
                      <JsonView value={tool.outputSchema} />
                    </TabsContent>
                  )}
                  <TabsContent value="run" className="pt-2">
                    <RunToolForm teamId={teamId} slug={connector.slug} tool={tool} />
                  </TabsContent>
                </Tabs>
              </div>
            </ScrollArea>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}
