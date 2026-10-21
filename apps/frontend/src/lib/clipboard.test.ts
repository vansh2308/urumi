import { afterEach, describe, expect, it, vi } from "vitest"

import { copyText } from "./clipboard"

/** The parts of a <textarea> the legacy path touches. */
function fakeTextarea() {
  return {
    value: "",
    style: {},
    setAttribute: vi.fn(),
    focus: vi.fn(),
    select: vi.fn(),
    setSelectionRange: vi.fn(),
    remove: vi.fn(),
  }
}

function stubDom({ copyResult = true } = {}) {
  const textarea = fakeTextarea()
  const body = { appendChild: vi.fn() }
  const previous = { focus: vi.fn() }
  const document = {
    body,
    activeElement: previous,
    createElement: vi.fn(() => textarea),
    execCommand: vi.fn(() => copyResult),
  }
  vi.stubGlobal("document", document)
  return { textarea, body, previous, document }
}

describe("copyText", () => {
  afterEach(() => vi.unstubAllGlobals())

  it("uses the Clipboard API in a secure context", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal("window", { isSecureContext: true })
    vi.stubGlobal("navigator", { clipboard: { writeText } })
    const { document } = stubDom()

    await copyText("urk_secret")

    expect(writeText).toHaveBeenCalledWith("urk_secret")
    expect(document.execCommand).not.toHaveBeenCalled()
  })

  it("falls back to a selected textarea on plain HTTP (no navigator.clipboard)", async () => {
    vi.stubGlobal("window", { isSecureContext: false })
    vi.stubGlobal("navigator", {})
    const { textarea, previous, document } = stubDom()
    const container = { appendChild: vi.fn() }

    await copyText("http://gateway.urumi.local/v1/mcp", container as unknown as HTMLElement)

    expect(container.appendChild).toHaveBeenCalledWith(textarea)
    expect(textarea.value).toBe("http://gateway.urumi.local/v1/mcp")
    expect(textarea.select).toHaveBeenCalled()
    expect(document.execCommand).toHaveBeenCalledWith("copy")
    expect(textarea.remove).toHaveBeenCalled()
    expect(previous.focus).toHaveBeenCalled()
  })

  it("appends to <body> when no container is given", async () => {
    vi.stubGlobal("window", { isSecureContext: false })
    vi.stubGlobal("navigator", {})
    const { textarea, body } = stubDom()

    await copyText("abc")

    expect(body.appendChild).toHaveBeenCalledWith(textarea)
  })

  it("rejects, and still cleans up, when the browser refuses to copy", async () => {
    vi.stubGlobal("window", { isSecureContext: false })
    vi.stubGlobal("navigator", {})
    const { textarea } = stubDom({ copyResult: false })

    await expect(copyText("abc")).rejects.toThrow("refused")
    expect(textarea.remove).toHaveBeenCalled()
  })
})
