const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" })
const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" })
const number = new Intl.NumberFormat()

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—"
  return dateTime.format(new Date(value))
}

export function formatRelative(value: string | null | undefined): string {
  if (!value) return "Never"
  const seconds = Math.round((new Date(value).getTime() - Date.now()) / 1000)
  const abs = Math.abs(seconds)
  if (abs < 60) return relative.format(seconds, "second")
  if (abs < 3600) return relative.format(Math.round(seconds / 60), "minute")
  if (abs < 86400) return relative.format(Math.round(seconds / 3600), "hour")
  if (abs < 86400 * 30) return relative.format(Math.round(seconds / 86400), "day")
  return dateTime.format(new Date(value))
}

export function formatNumber(value: number | null | undefined): string {
  return value == null ? "—" : number.format(value)
}

export function formatMs(value: number | null | undefined): string {
  if (value == null) return "—"
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)} s`
  return `${Math.round(value)} ms`
}

export function formatPercent(part: number, total: number): string {
  if (!total) return "—"
  return `${((part / total) * 100).toFixed(1)}%`
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "—"
}

/** ISO string for a date `days` ago, for since= filters. */
export function daysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString()
}
