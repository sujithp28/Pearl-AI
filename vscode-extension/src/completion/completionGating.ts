/**
 * Decides whether a completion is worth requesting at a cursor position.
 *
 * Pure (no `vscode` dependency), so it is directly unit testable —
 * the same split `timeline.ts` uses. This is the logic most worth
 * testing in the completion path: every false negative spends an
 * inference on the typing path, and every false positive puts ghost
 * text where the developer does not want it, and neither shows up as a
 * compile error.
 */

/**
 * True when a completion here would be noise rather than help.
 *
 * @param linePrefix      text on the current line before the cursor
 * @param charAfterCursor text on the current line after the cursor
 */
export function shouldSkipCompletion(
  linePrefix: string,
  charAfterCursor: string
): boolean {
  // Nothing typed on this line yet: any suggestion is a guess about what
  // the developer is about to start, not a continuation of anything.
  if (!linePrefix.trim()) {
    return true;
  }

  // Mid-word. VS Code's own word completion owns this case, and ghost
  // text competing with it reads as flicker.
  if (/\w$/.test(linePrefix) && /^\w/.test(charAfterCursor)) {
    return true;
  }

  // Inside a line comment — Pearl completes code, not prose, and a
  // half-written comment is the one place a code model reliably
  // produces something irrelevant.
  const trimmed = linePrefix.trimStart();
  if (
    trimmed.startsWith("//") ||
    trimmed.startsWith("#") ||
    trimmed.startsWith("*")
  ) {
    return true;
  }

  return false;
}
