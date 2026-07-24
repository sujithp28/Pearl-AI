/**
 * The minimal subset of Node's `ChildProcess` this extension needs.
 *
 * Declared as a narrow structural interface (rather than importing
 * `child_process.ChildProcess` directly everywhere) so tests can
 * supply a lightweight fake process without spawning anything real.
 */

export interface ReadableLike {
  on(event: "data", listener: (chunk: Buffer | string) => void): void;
}

export interface WritableLike {
  write(chunk: string): unknown;
}

export interface ChildProcessLike {
  stdin: WritableLike | null;
  stdout: ReadableLike | null;
  stderr: ReadableLike | null;
  on(
    event: "exit",
    listener: (code: number | null, signal: string | null) => void
  ): void;
  on(event: "error", listener: (error: Error) => void): void;
  kill(): void;
}

export type SpawnFn = (
  command: string,
  args: string[],
  options: { cwd?: string }
) => ChildProcessLike;
