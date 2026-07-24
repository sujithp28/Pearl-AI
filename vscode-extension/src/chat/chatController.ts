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
import { PlannedStep, planOnly } from "../mcp/planClient";
import { RequestSender } from "../mcp/requestSender";
import { ToolCallResult, callTool } from "../mcp/toolCallClient";
import { ToolApprover } from "./approval";
import { renderMarkdownToHtml } from "./markdown";
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
  | { type: "timeline"; stage: TimelineStage | null };

export type PostToWebview = (message: WebviewMessage) => void;

const NO_TOOL = "none";

export class ChatController {
  private readonly history: ChatMessage[] = [];

  constructor(
    private readonly connection: RequestSender,
    private readonly post: PostToWebview,
    private readonly approveTool: ToolApprover,
    private readonly approvePlan: PlanApprover
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
