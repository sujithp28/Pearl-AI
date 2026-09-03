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
 * on a network round trip), tool-execution-timeline stage updates
 * (Planning... / Plan Ready / Waiting for Approval / Running
 * Tool... / Completed), and — while an autonomous run (`runAutonomous`
 * below) is actually in flight — live `pearl/progress` updates
 * (current step / total steps / current action, streamed from the
 * server via `RequestSender.onNotification`) — pure UI/UX signals
 * the webview renders; they don't change any control flow above.
 *
 * Deliberately independent of the real `vscode.Webview` /
 * `MCPConnection` types (structural `RequestSender` + injectable
 * `ToolApprover`/`PlanApprover` only), so this — the actual behavior
 * worth testing — is unit testable without a webview, a real MCP
 * connection, or real approval UI. `RequestSender.onNotification` is
 * itself optional on that structural interface for the same reason —
 * a fake sender that doesn't implement it still satisfies the type,
 * and progress simply never streams for it.
 */

import { sendChatMessageStream } from "../mcp/chatClient";
import {
  ExecutionReportResult,
  ReflectionSummary,
  VerificationSummary,
  approvePatches,
  rejectPatches,
  runAutonomous as requestAutonomousRun,
} from "../mcp/patchClient";
import { PlannedStep, planOnly } from "../mcp/planClient";
import { TimelineLabels, fetchTimelineLabels } from "../mcp/personalityClient";
import { ProgressEvent, parseProgressEvent } from "../mcp/progressClient";
import { RequestSender } from "../mcp/requestSender";
import { ToolCallResult, callTool } from "../mcp/toolCallClient";
import { ToolApprover } from "./approval";
import { describeConnectionError } from "./errorReporting";
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
  | { type: "startStream" }
  | { type: "appendChunk"; chunk: string }
  | { type: "finalizeStream"; text: string; html: string; timestamp: string }
  | { type: "loading"; show: boolean }
  | { type: "timeline"; stage: TimelineStage | null; labels?: TimelineLabels }
  | { type: "executionState"; state: ExecutionState | null }
  | { type: "progress"; event: ProgressEvent | null };

export type PostToWebview = (message: WebviewMessage) => void;

const NO_TOOL = "none";

// Words/phrases that are always conversational and never require a tool.
// Matched case-insensitively after stripping leading/trailing punctuation.
const CONVERSATIONAL_RE =
  /^(hi+|hey+|hello+|hiya|howdy|greetings|good\s+(morning|afternoon|evening|night)|thanks?|thank\s+you|ty|cheers|great|perfect|awesome|cool|ok+a*y*|sure|got\s+it|makes?\s+sense|sounds?\s+good|wow|nice|interesting|what\s+(can|do)\s+you\s+do|how\s+do\s+you\s+work|what\s+is\s+pearl|who\s+are\s+you|are\s+you\s+there|really|yep|nope|yes|no|lol|haha|hmm+|what['’s]*\s+(is\s+)?(your\s+)?(home|workspace|working|project|root|current)\s+(path|dir(ectory)?|folder|location)?|where\s+(is|are)\s+you|what['’s]*\s+(your\s+)?(path|workspace|working\s+dir(ectory)?)|what\s+path)[!?.,\s]*$/i;

function isConversational(text: string): boolean {
  return CONVERSATIONAL_RE.test(text.trim());
}

export class ChatController {
  private readonly history: ChatMessage[] = [];
  // Fire-and-forget, not awaited on the message-handling critical
  // path: `postTimeline` uses whatever's in `timelineLabels` *right
  // now* (null until the first fetch resolves) rather than blocking
  // every message on a round trip first. Personality is a per-session
  // server setting, not something that changes mid-conversation in
  // normal use, so fetching once and reusing it — rather than
  // re-fetching before every message — is the right trade: the very
  // first timeline update of a session may render in the static
  // English fallback for a moment, every one after that (typically
  // within well under a second) uses the real wording.
  private timelineLabels: TimelineLabels | null = null;
  private timelineLabelsFetchStarted = false;

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

  private ensureTimelineLabelsFetching(): void {
    if (this.timelineLabelsFetchStarted) {
      return;
    }

    this.timelineLabelsFetchStarted = true;

    fetchTimelineLabels(this.connection)
      .then((labels) => {
        this.timelineLabels = labels;
      })
      .catch(() => {
        // Leave timelineLabels as null — the webview already falls
        // back to its own static English labels for that case.
      });
  }

  async handleUserMessage(text: string): Promise<void> {
    const trimmed = text.trim();

    if (!trimmed) {
      return;
    }

    this.addAndPost("user", trimmed);

    // Fast-path: skip the planner entirely for obvious conversational
    // messages (greetings, questions about Pearl, short social phrases).
    // These are guaranteed to produce tool:"none" plans, so calling
    // planOnly first just adds an extra LLM round-trip with no benefit.
    if (isConversational(trimmed)) {
      this.postLoading(true);
      await this.replyConversationally(trimmed);
      return;
    }

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
        const detail = describeConnectionError(error);
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
    // Callers already post loading(true); we only post loading(false)
    // once the stream's first chunk arrives (or on error).
    try {
      let started = false;
      let accumulated = "";

      const fullReply = await sendChatMessageStream(
        this.connection,
        text,
        (chunk) => {
          accumulated += chunk;
          if (!started) {
            // First chunk: dismiss loading dots, open streaming bubble.
            started = true;
            this.postLoading(false);
            this.post({ type: "startStream" });
          }
          this.post({ type: "appendChunk", chunk });
        }
      );

      if (!started) {
        // Provider fell back to non-streaming (no chunks fired).
        // Use the final reply from the request result directly.
        this.postLoading(false);
        accumulated = fullReply;
        this.post({ type: "startStream" });
      }

      // Finalize with fully markdown-rendered HTML.
      this.post({
        type: "finalizeStream",
        text: accumulated,
        html: renderMarkdownToHtml(accumulated),
        timestamp: new Date().toISOString(),
      });
    } catch (error) {
      this.postLoading(false);
      this.addAndPost("error", describeConnectionError(error));
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

    const approvePatch = this.approvePatch;

    if (!approvePatch) {
      this.addAndPost(
        "error",
        "Autonomous execution with patch approval is not configured."
      );
      return;
    }

    this.addAndPost("user", trimmed);

    // Subscribed for the whole run (including every approve/reject
    // round trip below, each of which can itself resume execution
    // and emit further events) and torn down unconditionally in
    // `finally` — a handler must never outlive the run that
    // registered it, or a later, unrelated run's UI could receive a
    // stale run's events.
    const unsubscribe = this.connection.onNotification?.(
      "pearl/progress",
      (params) => this.handleProgressNotification(params)
    );

    try {
      await this.runAutonomousToCompletion(trimmed, approvePatch);
    } finally {
      unsubscribe?.();
      this.postProgress(null);
    }
  }

  private handleProgressNotification(params: Record<string, unknown>): void {
    const event = parseProgressEvent(params);

    if (event) {
      this.postProgress(event);
    }
  }

  private postProgress(event: ProgressEvent | null): void {
    this.post({ type: "progress", event });
  }

  private async runAutonomousToCompletion(
    trimmed: string,
    approvePatch: PatchApprover
  ): Promise<void> {
    this.postLoading(true);

    let report: ExecutionReportResult;

    try {
      report = await requestAutonomousRun(this.connection, trimmed);
    } catch (error) {
      this.postLoading(false);
      this.addAndPost("error", describeConnectionError(error));
      return;
    }

    this.postLoading(false);

    while (report.stopReason === "awaiting_approval") {
      this.postExecutionState("awaiting_approval");

      const decision = await approvePatch(report.patches);

      if (decision === "reject") {
        this.postLoading(true);

        try {
          report = await rejectPatches(this.connection);
        } catch (error) {
          this.postLoading(false);
          const detail = describeConnectionError(error);
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
        const detail = describeConnectionError(error);
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
    const lines = [
      `Done. ${succeeded}/${report.steps.length} step(s) completed successfully.`,
    ];

    if (report.replansUsed) {
      lines.push(
        `Replanned ${report.replansUsed} time${report.replansUsed === 1 ? "" : "s"}.`
      );
    }

    lines.push(...this.formatVerification(report.verification));
    lines.push(...this.formatReflection(report.reflection));

    return lines.join("\n");
  }

  /**
   * Render the post-apply verification block, or nothing when
   * verification did not run — never claiming success for an
   * unverified change.
   */
  private formatVerification(verification?: VerificationSummary): string[] {
    if (!verification) {
      return [];
    }

    const lines = [`\n**Verification:** ${verification.status}`];

    if (verification.testsRun > 0) {
      lines.push(
        `- Tests: ${verification.testsPassed}/${verification.testsRun} passed`
      );
    } else {
      lines.push(`- Tests: none selected for the changed files`);
    }

    if (verification.changedFiles.length > 0) {
      lines.push(`- Changed: ${verification.changedFiles.join(", ")}`);
    }

    // A file changed but never planned is a correctness signal worth
    // surfacing, not noise to hide.
    if (verification.unexpectedFiles.length > 0) {
      lines.push(
        `- ⚠️ Unexpected changes: ${verification.unexpectedFiles.join(", ")}`
      );
    }

    return lines;
  }

  /**
   * Render the agent's own completion judgement. A "blocked" or
   * "replan" verdict must read as unfinished, not be dressed up as
   * success just because the tools themselves ran without error.
   */
  private formatReflection(reflection?: ReflectionSummary): string[] {
    if (!reflection) {
      return [];
    }

    const badge =
      reflection.status === "complete"
        ? "✓ complete"
        : reflection.status === "blocked"
          ? "✗ blocked"
          : `⚠ ${reflection.status}`;

    const lines = [
      `\n**Reflection:** ${badge} (${Math.round(reflection.confidence * 100)}% confidence)`,
    ];

    if (reflection.reason) {
      lines.push(`- ${reflection.reason}`);
    }

    if (reflection.missingRequirements.length > 0) {
      lines.push(
        `- Still needed: ${reflection.missingRequirements.join(", ")}`
      );
    }

    return lines;
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
    if (stage) {
      this.ensureTimelineLabelsFetching();
    }

    this.post({
      type: "timeline",
      stage,
      labels: this.timelineLabels ?? undefined,
    });
  }
}
