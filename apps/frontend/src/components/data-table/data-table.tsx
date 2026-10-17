import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
  type SortingState,
  type VisibilityState,
} from "@tanstack/react-table"
import {
  ArrowDownIcon,
  ArrowUpDownIcon,
  ArrowUpIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  Columns3Icon,
  SearchIcon,
} from "lucide-react"
import { useMemo, useState, type ReactNode } from "react"

import { EmptyState, ErrorAlert } from "@/components/common/query-state"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useSearchState } from "@/hooks/use-search-state"
import { cn } from "@/lib/utils"

export interface CursorPagination {
  hasPrevious: boolean
  hasNext: boolean
  onPrevious: () => void
  onNext: () => void
  pageLabel?: string
}

interface DataTableProps<T> {
  columns: ColumnDef<T, unknown>[]
  data: T[] | undefined
  isLoading?: boolean
  error?: unknown
  onRetry?: () => void
  /** Client-side search over all columns; state lives in ?q=. Omit to hide the search box. */
  searchPlaceholder?: string
  toolbar?: ReactNode
  emptyTitle?: string
  emptyDescription?: ReactNode
  emptyAction?: ReactNode
  /** Server-side (cursor) paging. Without it, rows are paged client-side. */
  cursor?: CursorPagination
  /** Prefix for the URL params, when a page shows more than one table. */
  urlKey?: string
  initialHidden?: VisibilityState
  onRowClick?: (row: T) => void
  getRowId?: (row: T) => string
}

const PAGE_SIZES = [10, 20, 50, 100]

export function DataTable<T>({
  columns,
  data,
  isLoading,
  error,
  onRetry,
  searchPlaceholder,
  toolbar,
  emptyTitle = "Nothing here yet",
  emptyDescription,
  emptyAction,
  cursor,
  urlKey = "",
  initialHidden = {},
  onRowClick,
  getRowId,
}: DataTableProps<T>) {
  const { get, set } = useSearchState()
  const k = (name: string) => (urlKey ? `${urlKey}_${name}` : name)

  const sortParam = get(k("sort"))
  const sorting: SortingState = useMemo(() => {
    if (!sortParam) return []
    const desc = sortParam.startsWith("-")
    return [{ id: desc ? sortParam.slice(1) : sortParam, desc }]
  }, [sortParam])
  const globalFilter = get(k("q")) ?? ""
  const pagination: PaginationState = {
    pageIndex: Math.max(0, Number(get(k("page")) ?? 1) - 1),
    pageSize: Number(get(k("size")) ?? 20),
  }
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(initialHidden)

  const table = useReactTable({
    data: data ?? [],
    columns,
    getRowId,
    state: { sorting, globalFilter, pagination, columnVisibility },
    onSortingChange: (updater) => {
      const next = typeof updater === "function" ? updater(sorting) : updater
      const first = next[0]
      set({ [k("sort")]: first ? `${first.desc ? "-" : ""}${first.id}` : null })
    },
    onGlobalFilterChange: (value) => set({ [k("q")]: String(value ?? ""), [k("page")]: null }),
    onPaginationChange: (updater) => {
      const next = typeof updater === "function" ? updater(pagination) : updater
      set({
        [k("page")]: next.pageIndex > 0 ? next.pageIndex + 1 : null,
        [k("size")]: next.pageSize !== 20 ? next.pageSize : null,
      })
    },
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: cursor ? undefined : getPaginationRowModel(),
    manualPagination: Boolean(cursor),
    autoResetPageIndex: false,
  })

  const hideable = table.getAllColumns().filter((c) => c.getCanHide() && c.columnDef.header)
  const rows = table.getRowModel().rows

  return (
    <div className="space-y-3">
      {(searchPlaceholder || toolbar || hideable.length > 0) && (
        <div className="flex flex-wrap items-center gap-2">
          {searchPlaceholder && (
            <InputGroup className="w-full sm:w-72">
              <InputGroupAddon>
                <SearchIcon />
              </InputGroupAddon>
              <InputGroupInput
                placeholder={searchPlaceholder}
                value={globalFilter}
                onChange={(e) => table.setGlobalFilter(e.target.value)}
                aria-label="Search"
              />
            </InputGroup>
          )}
          {toolbar}
          {hideable.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="ml-auto">
                  <Columns3Icon />
                  Columns
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>Visible columns</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {hideable.map((column) => (
                  <DropdownMenuCheckboxItem
                    key={column.id}
                    checked={column.getIsVisible()}
                    onCheckedChange={(v) => column.toggleVisibility(Boolean(v))}
                    onSelect={(e) => e.preventDefault()}
                  >
                    {typeof column.columnDef.header === "string" ? column.columnDef.header : column.id}
                  </DropdownMenuCheckboxItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      )}

      {error ? (
        <ErrorAlert error={error} title="Could not load data" onRetry={onRetry} />
      ) : !isLoading && rows.length === 0 ? (
        <EmptyState
          title={globalFilter ? "No matching results" : emptyTitle}
          description={globalFilter ? "Try a different search." : emptyDescription}
          action={globalFilter ? undefined : emptyAction}
        />
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table>
            <TableHeader>
              {table.getHeaderGroups().map((group) => (
                <TableRow key={group.id}>
                  {group.headers.map((header) => {
                    const canSort = header.column.getCanSort()
                    const sorted = header.column.getIsSorted()
                    const content = header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())
                    return (
                      <TableHead
                        key={header.id}
                        aria-sort={
                          sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined
                        }
                      >
                        {canSort ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="-ml-2.5 h-7"
                            onClick={header.column.getToggleSortingHandler()}
                          >
                            {content}
                            {sorted === "asc" ? (
                              <ArrowUpIcon />
                            ) : sorted === "desc" ? (
                              <ArrowDownIcon />
                            ) : (
                              <ArrowUpDownIcon className="opacity-40" />
                            )}
                          </Button>
                        ) : (
                          content
                        )}
                      </TableHead>
                    )
                  })}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {isLoading
                ? Array.from({ length: 5 }).map((_, i) => (
                    <TableRow key={i}>
                      {table.getVisibleLeafColumns().map((c) => (
                        <TableCell key={c.id}>
                          <Skeleton className="h-4 w-full max-w-40" />
                        </TableCell>
                      ))}
                    </TableRow>
                  ))
                : rows.map((row) => (
                    <TableRow
                      key={row.id}
                      onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                      className={cn(onRowClick && "cursor-pointer")}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <TableCell key={cell.id}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
            </TableBody>
          </Table>
        </div>
      )}

      {!error && (cursor ? (
        <div className="flex items-center justify-end gap-2">
          {cursor.pageLabel && (
            <span className="text-sm text-muted-foreground">{cursor.pageLabel}</span>
          )}
          <Button variant="outline" size="sm" disabled={!cursor.hasPrevious} onClick={cursor.onPrevious}>
            <ChevronLeftIcon />
            Newer
          </Button>
          <Button variant="outline" size="sm" disabled={!cursor.hasNext} onClick={cursor.onNext}>
            Older
            <ChevronRightIcon />
          </Button>
        </div>
      ) : (
        table.getFilteredRowModel().rows.length > PAGE_SIZES[0] && (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm text-muted-foreground">
              {table.getFilteredRowModel().rows.length} rows
            </span>
            <div className="flex items-center gap-2">
              <Select
                value={String(pagination.pageSize)}
                onValueChange={(v) => table.setPagination({ pageIndex: 0, pageSize: Number(v) })}
              >
                <SelectTrigger size="sm" className="w-20" aria-label="Rows per page">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PAGE_SIZES.map((s) => (
                    <SelectItem key={s} value={String(s)}>
                      {s}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span className="text-sm text-muted-foreground">
                Page {pagination.pageIndex + 1} of {Math.max(1, table.getPageCount())}
              </span>
              <Button
                variant="outline"
                size="icon-sm"
                disabled={!table.getCanPreviousPage()}
                onClick={() => table.previousPage()}
                aria-label="Previous page"
              >
                <ChevronLeftIcon />
              </Button>
              <Button
                variant="outline"
                size="icon-sm"
                disabled={!table.getCanNextPage()}
                onClick={() => table.nextPage()}
                aria-label="Next page"
              >
                <ChevronRightIcon />
              </Button>
            </div>
          </div>
        )
      ))}
    </div>
  )
}
