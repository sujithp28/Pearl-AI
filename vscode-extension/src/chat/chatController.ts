/**
 * Drives the chat webview's message flow.
 *
 * For each user message:
 *   1. Ask the existing Planner (via `pearl/planOnly`) what it
 *      would do, without executing anything yet.
 *   2. If no tool is needed, reply conversationally (via the
 *      existing `pearl/chat`), unchanged from before this feature.
 *   3. Otherwise, before requesting any per-tool approval, show the
 *      complete plan and ask for a single Execute Plan / Cancel
 *      decision. Cancel stops here — no tool approvals or tool
 *      executions occur. Execute Plan continues into the existing
 *      per-tool approval loop, unchanged.
 *   4. For each proposed tool step, request approval (name +
 *      arguments) before ever calling `tools/call`. A rejection
 *      cancels the rest of the plan cleanly and reports that back
 *      to the chat — no tool call is ever sent for a rejected step,
 *      and no further steps run after a rejection.
 *
 * Along the way, this also posts loading indicators (while waiting
 * on a network round trip) and tool-execution-timeline stage
 * updates (Planning... / Plan Ready / Waiting for Approval /
 * Running Tool... / Completed) — pure UI/UX signals the webview
 * renders; they don't change any control flow above.
 *
 * Deliberately independent of the real `vscode.Webview` /
 * `MCPConnection` types (structural `RequestSender` + injectable
 * `ToolApprover`/`PlanApprover` only), so this — the actual behavior
 * worth testing — is unit testable without a webview, a real MCP
 * connection, or real approval UI.
 */

import { sendChatMessage } from "../mcp/chatClient";
import {
  ExecutionReportResult,
  approvePatches,
  rejectPatches,
  runAutonomous as requestAutonomousRun,
} from "../mcp/patchClient";
import { PlannedStep, planOnly } from "../mcp/planClient";
import { RequestSender } from "../mcp/requestSender";
import { ToolCallResult, callTool } from "../mcp/toolCallClient";
import { ToolApprover } from "./approval";
import { ExecutionState } from "./executionState";
import { renderMarkdownToHtml } from "./markdown";
import { PatchApprover } from "./patchApproval";
import { PlanApprover } from "./planApproval";
import { TimelineStage } from "./timeline";

export type ChatRole = "user" | "assistant" | "error";

export interface ChatMessage {
  role: ChatRole;
  text: string;
  html: string;
  timestamp: string;
}

export type WebviewMessage =
  | { type: "addMessage"; message: ChatMessage }
  | { type: "loading"; show: boolean }
  | { type: "timeline"; stage: TimelineStage | null }
  | { type: "executionState"; state: ExecutionState | null };

export type PostToWebview = (message: WebviewMessage) => void;

const NO_TOOL = "none";

export class ChatController {
  private readonly history: ChatMessage[] = [];

  constructor(
    private readonly connection: RequestSender,
    private readonly post: PostToWebview,
    private readonly approveTool: ToolApprover,
    private readonly approvePlan: PlanApprover,
    private readonly approvePatch?: PatchApprover
  ) {}

  getHistory(): readonly ChatMessage[] {
    return this.history;
  }

  async handleUserMessage(text: string): Promise<void> {
    const trimmed = text.trim();

    if (!trimmed) {
      return;
    }

    this.addAndPost("user", trimmed);
    this.postTimeline("planning");
    this.postLoading(true);

    let steps: PlannedStep[];

    try {
      steps = await planOnly(this.connection, trimmed);
    } catch {
      // Planning isn't available/failed: fall back to a plain
      // conversational reply, same as before this feature existed.
      this.postTimeline(null);
      await this.replyConversationally(trimmed);
      return;
    }

    const actionable = steps.filter((step) => step.tool !== NO_TOOL);

    if (actionable.length === 0) {
      this.postTimeline(null);
      await this.replyConversationally(trimmed);
      return;
    }

    this.postTimeline("plan_ready");
    this.postLoading(false);
    this.postTimeline("waiting_approval");

    const planDecision = await this.approvePlan(actionable);

    if (planDecision === "cancel") {
      this.postTimeline(null);
      this.addAndPost(
        "assistant",
        "Plan cancelled. No changes were made."
      );
      return;
    }

    await this.runApprovedSteps(actionable);
  }

  private async runApprovedSteps(steps: PlannedStep[]): Promise<void> {
    for (const step of steps) {
      this.postTimeline("waiting_approval");

      const decision = await this.approveTool({
        tool: step.tool,
        arguments: step.arguments,
      });

      if (decision === "rejected") {
        this.postTimeline(null);
        this.addAndPost(
          "assistant",
          `Tool "${step.tool}" was rejected. No changes were made.`
        );
        return;
      }

      this.postTimeline("running_tool");
      this.postLoading(true);

      let result: ToolCallResult;

      try {
        result = await callTool(this.connection, step.tool, step.arguments);
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        this.postTimeline(null);
        this.postLoading(false);
        this.addAndPost("error", `Failed to run "${step.tool}": ${detail}`);
        return;
      }

      this.postLoading(false);

      this.addAndPost(
        result.isError ? "error" : "assistant",
        this.formatToolResult(step.tool, result)
      );

      if (result.isError) {
        this.postTimeline(null);
        return;
      }
    }

    this.postTimeline("completed");
  }

  private async replyConversationally(text: string): Promise<void> {
    // Callers already post loading(true) before deciding to fall
    // back here (planning itself is also a network round trip); we
    // only need to post the closing loading(false) once this
    // request settles.
    try {
      const reply = await sendChatMessage(this.connection, text);
      this.addAndPost("assistant", reply);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      this.addAndPost("error", `Failed to reach Pearl: ${detail}`);
    } finally {
      this.postLoading(false);
    }
  }

  /**
   * Run `prompt` through Pearl's autonomous executor (`pearl/
   * runAutonomous`), which stages any file edits it wants to make
   * via `PatchManager` instead of writing them directly. Whenever
   * the run pauses with a patch batch awaiting approval, this shows
   * it (via `approvePatch`) and either resumes execution — via
   * `pearl/approvePatches`, which writes the patches and continues
   * from exactly where it paused, without re-planning or re-running
   * completed steps — or discards it via `pearl/rejectPatches` and
   * stops cleanly. Repeats for as many approval rounds as the run
   * produces.
   *
   * Requires `approvePatch` to have been supplied at construction;
   * without it (e.g. a caller not wired up for this feature yet)
   * this reports a clear error instead of a silent no-op.
   */
  async runAutonomous(prompt: string): Promise<void> {
    const trimmed = prompt.trim();

    if (!trimmed) {
      return;
    }

    if (!this.approvePatch) {
      this.addAndPost(
        "error",
        "Autonomous execution with patch approval is not configured."
      );
      return;
    }

    this.addAndPost("user", trimmed);
    this.postLoading(true);

    let report: ExecutionReportResult;

    try {
      report = await requestAutonomousRun(this.connection, trimmed);
    } catch (error) {
      this.postLoading(false);
      const detail = error instanceof Error ? error.message : String(error);
      this.addAndPost("error", `Failed to reach Pearl: ${detail}`);
      return;
    }

    this.postLoading(false);

    while (report.stopReason === "awaiting_approval") {
      this.postExecutionState("awaiting_approval");

      const decision = await this.approvePatch(report.patches);

      if (decision === "reject") {
        this.postLoading(true);

        try {
          report = await rejectPatches(this.connection);
        } catch (error) {
          this.postLoading(false);
          const detail =
            error instanceof Error ? error.message : String(error);
          this.addAndPost("error", `Failed to reject patches: ${detail}`);
          this.postExecutionState(null);
          return;
        }

        this.postLoading(false);
        this.finalizeExecutionReport(report);
        return;
      }

      this.postExecutionState("applying_patches");
      this.postLoading(true);

      try {
        report = await approvePatches(this.connection);
      } catch (error) {
        this.postLoading(false);
        const detail = error instanceof Error ? error.message : String(error);
        this.addAndPost("error", `Failed to resume execution: ${detail}`);
        this.postExecutionState(null);
        return;
      }

      this.postLoading(false);
      this.postExecutionState("resuming");
    }

    this.finalizeExecutionReport(report);
  }

  private finalizeExecutionReport(report: ExecutionReportResult): void {
    if (report.stopReason === "completed") {
      this.postExecutionState("completed");
      this.addAndPost("assistant", this.formatExecutionSummary(report));
    } else if (report.stopReason === "cancelled") {
      this.postExecutionState("cancelled");
      this.addAndPost("assistant", "Execution was cancelled.");
    } else if (report.stopReason === "rejected") {
      this.postExecutionState("cancelled");
      this.addAndPost(
        "assistant",
        "Patches rejected. No changes were written."
      );
    } else {
      this.addAndPost(
        "error",
        `Execution stopped: ${report.stopReason}.`
      );
    }

    this.postExecutionState(null);
  }

  private formatExecutionSummary(report: ExecutionReportResult): string {
    const succeeded = report.steps.filter((step) => step.succeeded).length;
    return `Done. ${succeeded}/${report.steps.length} step(s) completed successfully.`;
  }

  private postExecutionState(state: ExecutionState | null): void {
    this.post({ type: "executionState", state });
  }

  private formatToolResult(tool: string, result: ToolCallResult): string {
    const text = result.content.map((block) => block.text).join("\n");
    return `Ran "${tool}":\n${text}`;
  }

  private addAndPost(role: ChatRole, text: string): void {
    const message: ChatMessage = {
      role,
      text,
      html: renderMarkdownToHtml(text),
      timestamp: new Date().toISOString(),
    };
    this.history.push(message);
    this.post({ type: "addMessage", message });
  }

  private postLoading(show: boolean): void {
    this.post({ type: "loading", show });
  }

  private postTimeline(stage: TimelineStage | null): void {
    this.post({ type: "timeline", stage });
  }
}
