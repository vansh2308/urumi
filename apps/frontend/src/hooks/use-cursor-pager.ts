import { useState } from "react"

/** Keeps the cursors of pages already visited so "newer" can go back. Resets when `resetKey`
 *  (the serialised filters) changes. */
export function useCursorPager(resetKey: string) {
  const [state, setState] = useState<{ key: string; stack: string[] }>({ key: resetKey, stack: [] })
  const stack = state.key === resetKey ? state.stack : []
  const cursor = stack.at(-1)
  return {
    cursor,
    page: stack.length + 1,
    next: (nextCursor: string) => setState({ key: resetKey, stack: [...stack, nextCursor] }),
    previous: () => setState({ key: resetKey, stack: stack.slice(0, -1) }),
    hasPrevious: stack.length > 0,
  }
}
