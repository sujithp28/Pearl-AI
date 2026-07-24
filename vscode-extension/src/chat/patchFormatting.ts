/**
 * Pure formatting for the patch-preview view: turns raw
 * `PatchFileSummary` objects (from `pearl/runAutonomous` /
 * `pearl/approvePatches`) into a simple, renderable shape — added/
 * removed line counts, a "large diff" collapse flag, and a guessed
 * language for basic diff-line highlighting. No `vscode` dependency,
 * so this (the actual counting/collapsing decisions) is directly
 * unit testable.
 */

import { PatchFileSummary } from "../mcp/patchClient";

export const DIFF_COLLAPSE_LINE_THRESHOLD = 40;

export interface DiffLineCounts {
  additions: number;
  removals: number;
}

export interface FormattedPatchFile {
  path: string;
  diff: string;
  isNewFile: boolean;
  additions: number;
  removals: number;
  collapsed: boolean;
  language: string;
}

/**
 * Count added/removed lines in a unified diff, ignoring the
 * `---`/`+++` file-header lines.
 */
export function countDiffLines(diff: string): DiffLineCounts {
  let additions = 0;
  let removals = 0;

  for (const line of diff.split("\n")) {
    if (line.startsWith("+++") || line.startsWith("---")) {
      continue;
    }

    if (line.startsWith("+")) {
      additions += 1;
    } else if (line.startsWith("-")) {
      removals += 1;
    }
  }

  return { additions, removals };
}

const EXTENSION_LANGUAGES: Record<string, string> = {
  py: "python",
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  json: "json",
  md: "markdown",
  html: "html",
  css: "css",
  sh: "bash",
  yml: "yaml",
  yaml: "yaml",
};

/**
 * Guess a language name from a file's extension, for a syntax-aware
 * hint (e.g. a `language-*` CSS class) where possible. Returns ""
 * for unrecognized/missing extensions.
 */
export function guessLanguage(path: string): string {
  const dotIndex = path.lastIndexOf(".");

  if (dotIndex === -1) {
    return "";
  }

  const extension = path.slice(dotIndex + 1).toLowerCase();

  return EXTENSION_LANGUAGES[extension] ?? "";
}

export function formatPatchFiles(
  files: PatchFileSummary[]
): FormattedPatchFile[] {
  return files.map((file) => {
    const { additions, removals } = countDiffLines(file.diff);
    const lineCount = file.diff.length === 0 ? 0 : file.diff.split("\n").length;

    return {
      path: file.path,
      diff: file.diff,
      isNewFile: file.isNewFile,
      additions,
      removals,
      collapsed: lineCount > DIFF_COLLAPSE_LINE_THRESHOLD,
      language: guessLanguage(file.path),
    };
  });
}
