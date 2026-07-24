/**
 * Manages the lifecycle of a connection to Pearl's existing MCP
 * server (`python -m src.mcp`, see `src/mcp/server.py`), run as a
 * child process communicating over stdio.
 *
 * This module does not implement chat or webviews, and it does not
 * modify the Python-side server in any way — it only speaks the
 * JSON-RPC protocol that server already exposes.
 */

import { MCPProtocolClient, JsonRpcResponseMessage, isErrorResponse } from "./protocolClient";
import { ChildProcessLike, SpawnFn } from "./processTypes";

export type ConnectionStatus =
  | "connecting"
  | "connected"
  | "disconnected"
  | "error";

export interface ConnectionOptions {
  command: string;
  args: string[];
  cwd?: string;
  spawnFn: SpawnFn;
  /** How long to wait for the server to answer `initialize`. */
  initializeTimeoutMs?: number;
  /** Delay before retrying after an unexpected exit or failed start. */
  reconnectDelayMs?: number;
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
}

export type StatusListener = (status: ConnectionStatus, detail?: string) => void;

export class MCPConnection {
  private readonly protocol = new MCPProtocolClient();
  private readonly pending = new Map<number, PendingRequest>();

  private child: ChildProcessLike | null = null;
  private stopped = true;
  private buffer = "";
  private status: ConnectionStatus = "disconnected";
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  public onStatusChange: StatusListener | undefined;
  public onStderr: ((chunk: string) => void) | undefined;

  constructor(private readonly options: ConnectionOptions) {}

  /**
   * Start (or restart) the connection.
   */
  start(): void {
    this.stopped = false;
    this.spawnProcess();
  }

  /**
   * Stop the connection. Any subsequent process exit is treated as
   * intentional and will not trigger a reconnect.
   */
  stop(): void {
    this.stopped = true;

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    if (this.child) {
      try {
        this.child.stdin?.write(this.protocol.buildNotification("exit"));
      } catch {
        // Best-effort: the process may already be gone.
      }

      try {
        this.child.kill();
      } catch {
        // Already dead.
      }

      this.child = null;
    }

    this.rejectAllPending(new Error("Connection stopped."));
    this.setStatus("disconnected");
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  /**
   * Send a JSON-RPC request and resolve with its result.
   */
  sendRequest(
    method: string,
    params?: Record<string, unknown>,
    timeoutMs = 10000
  ): Promise<unknown> {
    if (!this.child || !this.child.stdin) {
      return Promise.reject(
        new Error("Not connected to Pearl MCP server.")
      );
    }

    const { id, line } = this.protocol.buildRequest(method, params);
    const child = this.child;

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Timed out waiting for response to '${method}'.`));
      }, timeoutMs);

      this.pending.set(id, {
        resolve: (value) => {
          clearTimeout(timer);
          resolve(value);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      });

      child.stdin!.write(line);
    });
  }

  // -- Internals -----------------------------------------------------------

  private setStatus(status: ConnectionStatus, detail?: string): void {
    this.status = status;
    this.onStatusChange?.(status, detail);
  }

  private spawnProcess(): void {
    this.setStatus("connecting");
    this.buffer = "";

    let child: ChildProcessLike;

    try {
      child = this.options.spawnFn(this.options.command, this.options.args, {
        cwd: this.options.cwd,
      });
    } catch (error) {
      this.handleStartupFailure(error as Error);
      return;
    }

    this.child = child;

    child.on("error", (error) => {
      this.handleStartupFailure(error);
    });

    child.on("exit", (code) => {
      this.child = null;
      this.rejectAllPending(new Error("Pearl MCP server exited."));

      if (this.stopped) {
        this.setStatus("disconnected");
        return;
      }

      this.setStatus(
        "error",
        `Pearl MCP server exited unexpectedly (code ${code ?? "unknown"}).`
      );
      this.scheduleReconnect();
    });

    child.stdout?.on("data", (chunk) => {
      this.handleData(chunk.toString());
    });

    // Must be drained even if nobody wants the contents: Node still
    // pipes stderr by default, and an unread pipe fills up once the
    // server logs enough output, blocking the (synchronous) Python
    // process's next write and hanging every future request.
    child.stderr?.on("data", (chunk) => {
      this.onStderr?.(chunk.toString());
    });

    this.confirmStartup();
  }

  /**
   * Detect startup failures gracefully: a process that spawns but
   * never answers `initialize` (bad interpreter, missing module,
   * crash on import, ...) is treated the same as one that fails to
   * spawn at all.
   */
  private confirmStartup(): void {
    const timeoutMs = this.options.initializeTimeoutMs ?? 5000;

    this.sendRequest("initialize", {}, timeoutMs)
      .then(() => {
        if (!this.stopped) {
          this.setStatus("connected");
        }
      })
      .catch((error: Error) => {
        this.handleStartupFailure(error);
      });
  }

  private handleStartupFailure(error: Error): void {
    if (this.child) {
      try {
        this.child.kill();
      } catch {
        // Already dead.
      }
      this.child = null;
    }

    this.rejectAllPending(error);

    if (this.stopped) {
      this.setStatus("disconnected");
      return;
    }

    this.setStatus("error", error.message);
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer) {
      return;
    }

    const delay = this.options.reconnectDelayMs ?? 2000;

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;

      if (!this.stopped) {
        this.spawnProcess();
      }
    }, delay);
  }

  private handleData(text: string): void {
    this.buffer += text;

    const lines = this.buffer.split("\n");
    this.buffer = lines.pop() ?? "";

    for (const line of lines) {
      const response = this.protocol.parseResponseLine(line);

      if (response) {
        this.resolveResponse(response);
      }
    }
  }

  private resolveResponse(response: JsonRpcResponseMessage): void {
    if (typeof response.id !== "number") {
      return;
    }

    const pending = this.pending.get(response.id);

    if (!pending) {
      return;
    }

    this.pending.delete(response.id);

    if (isErrorResponse(response)) {
      pending.reject(new Error(response.error.message));
    } else {
      pending.resolve(response.result);
    }
  }

  private rejectAllPending(error: Error): void {
    for (const request of this.pending.values()) {
      request.reject(error);
    }

    this.pending.clear();
  }
}
