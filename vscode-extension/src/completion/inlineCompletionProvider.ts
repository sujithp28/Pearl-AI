/**
 * Ghost-text inline completions, backed by Pearl's `pearl/complete`.
 *
 * The hard part of inline completion is not producing a suggestion — it
 * is producing few enough of them. VS Code invokes a provider on nearly
 * every keystroke, and a provider that forwards each one produces a
 * queue of stale inferences competing to render, which is what makes a
 * completion feel laggy and erratic even when each individual call is
 * fast. Three rules avoid that:
 *
 *  1. Debounce — wait for a pause in typing before asking at all, so a
 *     burst of keystrokes costs one request, not one per character.
 *  2. Honour cancellation — VS Code cancels the previous token as soon
 *     as the next keystroke arrives; a cancelled request must not
 *     render, even if its response has already come back.
 *  3. Cheap local gates — skip contexts where a suggestion is noise
 *     (inside a comment, mid-word, empty line) without a round trip.
 *
 * Failures are always silent. An inline provider that surfaces errors
 * interrupts typing, which is worse than showing nothing.
 */

import * as vscode from "vscode";
import { RequestSender } from "../mcp/requestSender";
import { requestCompletion } from "../mcp/completionClient";
import { shouldSkipCompletion } from "./completionGating";

/**
 * How long typing must pause before a completion is requested.
 * Tuned against the local model's ~200-350ms latency: shorter than this
 * and a fast typist outruns every request; much longer and the
 * suggestion feels like it arrives after the thought.
 */
export const DEBOUNCE_MS = 150;

/** Text on either side of the cursor sent as context. */
const PREFIX_CHARS = 2000;
const SUFFIX_CHARS = 500;

export class PearlInlineCompletionProvider
  implements vscode.InlineCompletionItemProvider
{
  private debounceTimer: NodeJS.Timeout | undefined;

  constructor(
    private readonly sender: RequestSender,
    private readonly isEnabled: () => boolean = () => true
  ) {}

  async provideInlineCompletionItems(
    document: vscode.TextDocument,
    position: vscode.Position,
    _context: vscode.InlineCompletionContext,
    token: vscode.CancellationToken
  ): Promise<vscode.InlineCompletionItem[] | undefined> {
    if (!this.isEnabled()) {
      return undefined;
    }

    const linePrefix = document
      .lineAt(position.line)
      .text.substring(0, position.character);
    const lineSuffix = document
      .lineAt(position.line)
      .text.substring(position.character);

    if (shouldSkipCompletion(linePrefix, lineSuffix)) {
      return undefined;
    }

    // Wait for typing to pause. Resolves early with `false` if this
    // request is superseded, so a burst of keystrokes leaves only the
    // last one standing.
    const survived = await this.waitForPause(token);
    if (!survived || token.isCancellationRequested) {
      return undefined;
    }

    const offset = document.offsetAt(position);
    const fullText = document.getText();
    const prefix = fullText.substring(Math.max(0, offset - PREFIX_CHARS), offset);
    const suffix = fullText.substring(offset, offset + SUFFIX_CHARS);

    const result = await requestCompletion(
      this.sender,
      prefix,
      suffix,
      document.languageId
    );

    // Re-check after the await: the developer has very likely typed
    // again while this was in flight, and rendering now would insert a
    // suggestion computed for a cursor position that no longer exists.
    if (token.isCancellationRequested || !result || !result.completion) {
      return undefined;
    }

    return [
      new vscode.InlineCompletionItem(
        result.completion,
        new vscode.Range(position, position)
      ),
    ];
  }

  /**
   * Resolve `true` after DEBOUNCE_MS of quiet, or `false` immediately if
   * cancelled first.
   */
  private waitForPause(token: vscode.CancellationToken): Promise<boolean> {
    return new Promise((resolve) => {
      if (this.debounceTimer) {
        clearTimeout(this.debounceTimer);
      }

      const cancelListener = token.onCancellationRequested(() => {
        if (this.debounceTimer) {
          clearTimeout(this.debounceTimer);
          this.debounceTimer = undefined;
        }
        cancelListener.dispose();
        resolve(false);
      });

      this.debounceTimer = setTimeout(() => {
        cancelListener.dispose();
        this.debounceTimer = undefined;
        resolve(true);
      }, DEBOUNCE_MS);
    });
  }

  dispose(): void {
    if (this.debounceTimer) {
      clearTimeout(this.debounceTimer);
      this.debounceTimer = undefined;
    }
  }
}
