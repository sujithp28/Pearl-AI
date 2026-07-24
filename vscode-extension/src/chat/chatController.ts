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
import { PlanApprover } from "./planApproval";

export type ChatRole = "user" | "assistant" | "error";

export interface ChatMessage {
  role: ChatRole;
  text: string;
}

export type PostToWebview = (message: {
  type: "addMessage";
  message: ChatMessage;
}) => void;

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

    this.addAndPost({ role: "user", text: trimmed });

    let steps: PlannedStep[];

    try {
      steps = await planOnly(this.connection, trimmed);
    } catch {
      // Planning isn't available/failed: fall back to a plain
      // conversational reply, same as before this feature existed.
      await this.replyConversationally(trimmed);
      return;
    }

    const actionable = steps.filter((step) => step.tool !== NO_TOOL);

    if (actionable.length === 0) {
      await this.replyConversationally(trimmed);
      return;
    }

    const planDecision = await this.approvePlan(actionable);

    if (planDecision === "cancel") {
      this.addAndPost({
        role: "assistant",
        text: "Plan cancelled. No changes were made.",
      });
      return;
    }

    await this.runApprovedSteps(actionable);
  }

  private async runApprovedSteps(steps: PlannedStep[]): Promise<void> {
    for (const step of steps) {
      const decision = await this.approveTool({
        tool: step.tool,
        arguments: step.arguments,
      });

      if (decision === "rejected") {
        this.addAndPost({
          role: "assistant",
          text: `Tool "${step.tool}" was rejected. No changes were made.`,
        });
        return;
      }

      let result: ToolCallResult;

      try {
        result = await callTool(this.connection, step.tool, step.arguments);
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        this.addAndPost({
          role: "error",
          text: `Failed to run "${step.tool}": ${detail}`,
        });
        return;
      }

      this.addAndPost({
        role: result.isError ? "error" : "assistant",
        text: this.formatToolResult(step.tool, result),
      });

      if (result.isError) {
        return;
      }
    }
  }

  private async replyConversationally(text: string): Promise<void> {
    try {
      const reply = await sendChatMessage(this.connection, text);
      this.addAndPost({ role: "assistant", text: reply });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      this.addAndPost({
        role: "error",
        text: `Failed to reach Pearl: ${detail}`,
      });
    }
  }

  private formatToolResult(tool: string, result: ToolCallResult): string {
    const text = result.content.map((block) => block.text).join("\n");
    return `Ran "${tool}":\n${text}`;
  }

  private addAndPost(message: ChatMessage): void {
    this.history.push(message);
    this.post({ type: "addMessage", message });
  }
}
