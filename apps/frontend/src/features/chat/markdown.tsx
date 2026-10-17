import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"

// Styled with plain utility classes; no raw HTML is rendered (react-markdown escapes it).
const components: Components = {
  p: (p) => <p className="leading-relaxed [&:not(:first-child)]:mt-3" {...p} />,
  ul: (p) => <ul className="mt-2 ml-5 list-disc space-y-1" {...p} />,
  ol: (p) => <ol className="mt-2 ml-5 list-decimal space-y-1" {...p} />,
  h1: (p) => <h3 className="mt-4 text-base font-semibold" {...p} />,
  h2: (p) => <h3 className="mt-4 text-base font-semibold" {...p} />,
  h3: (p) => <h4 className="mt-3 font-semibold" {...p} />,
  a: (p) => <a className="underline underline-offset-4" target="_blank" rel="noreferrer noopener" {...p} />,
  code: ({ className, ...p }) => (
    <code className={`rounded bg-muted px-1 py-0.5 font-mono text-[0.85em] ${className ?? ""}`} {...p} />
  ),
  pre: (p) => <pre className="mt-2 overflow-x-auto rounded-md border bg-muted/40 p-3 text-xs [&_code]:bg-transparent [&_code]:p-0" {...p} />,
  table: (p) => (
    <div className="mt-2 overflow-x-auto rounded-md border">
      <table className="w-full text-sm" {...p} />
    </div>
  ),
  th: (p) => <th className="border-b bg-muted/40 px-2 py-1.5 text-left font-medium" {...p} />,
  td: (p) => <td className="border-b px-2 py-1.5 align-top" {...p} />,
  blockquote: (p) => <blockquote className="mt-2 border-l-2 pl-3 text-muted-foreground" {...p} />,
}

export function Markdown({ text }: { text: string }) {
  return (
    <div className="text-sm break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  )
}
