/**
 * Pure formatting for the plan-visualization view: turns the raw
 * steps from `pearl/planOnly` into a simple, renderable shape —
 * step number, tool name, and pretty-printed arguments, flagged for
 * collapsing when long. No `vscode` dependency, so this (the actual
 * "collapsed if long" decision) is directly unit testable.
 */

import { PlannedStep } from "../mcp/planClient";

export const PLAN_ARGS_COLLAPSE_THRESHOLD = 100;

export interface FormattedPlanStep {
  index: number;
  tool: string;
  argumentsText: string;
  collapsed: boolean;
}

export function formatPlanSteps(steps: PlannedStep[]): FormattedPlanStep[] {
  return steps.map((step, position) => {
    const argumentsText = JSON.stringify(step.arguments, null, 2);

    return {
      index: position + 1,
      tool: step.tool,
      argumentsText,
      collapsed: argumentsText.length > PLAN_ARGS_COLLAPSE_THRESHOLD,
    };
  });
}
