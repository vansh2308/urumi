import { useCallback } from "react"
import { useSearchParams } from "react-router"

/** Reads and writes URL search params so filters, sorting and pages are shareable links. */
export function useSearchState() {
  const [params, setParams] = useSearchParams()

  const set = useCallback(
    (updates: Record<string, string | number | boolean | null | undefined>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          for (const [key, value] of Object.entries(updates)) {
            if (value === null || value === undefined || value === "" || value === false) {
              next.delete(key)
            } else {
              next.set(key, String(value))
            }
          }
          return next
        },
        { replace: true }
      )
    },
    [setParams]
  )

  const get = useCallback((key: string) => params.get(key) ?? undefined, [params])

  return { params, get, set }
}
