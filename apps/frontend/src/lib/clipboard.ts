/** Copy text to the clipboard on any origin.
 *
 *  `navigator.clipboard` exists only in secure contexts (HTTPS, localhost), so it is missing on a
 *  plain-HTTP host such as http://urumi.local. There the text is copied the legacy way: select it
 *  in a hidden textarea and run the "copy" command, which still works inside a click handler.
 *
 *  `container` is where the textarea goes. Pass an element next to the clicked control: inside a
 *  dialog or sheet, the focus trap would pull focus (and the selection) back out of a textarea
 *  appended to <body>. */
export async function copyText(text: string, container?: HTMLElement | null): Promise<void> {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  legacyCopy(text, container ?? document.body)
}

function legacyCopy(text: string, container: HTMLElement): void {
  const textarea = document.createElement("textarea")
  textarea.value = text
  textarea.setAttribute("readonly", "")
  textarea.setAttribute("aria-hidden", "true")
  // Off-screen but rendered: a display:none element cannot be selected.
  Object.assign(textarea.style, { position: "fixed", top: "0", left: "-9999px", opacity: "0" })
  const previous = document.activeElement as HTMLElement | null
  container.appendChild(textarea)
  try {
    textarea.focus()
    textarea.select()
    textarea.setSelectionRange(0, text.length)
    if (!document.execCommand("copy")) {
      throw new Error("The browser refused to copy.")
    }
  } finally {
    textarea.remove()
    previous?.focus?.()
  }
}
