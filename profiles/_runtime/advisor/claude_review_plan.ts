import { spawn as defaultSpawn, type SpawnInput } from "./runner.ts";
import {
  type BusyResult,
  type OnBusy,
  singleFlight as defaultSingleFlight,
} from "./single_flight.ts";
import type { AdvisorFailReason, SpawnErr, SpawnOk } from "./types.ts";

const TOOL_NAME = "advisor.claude.review_plan";
const DEFAULT_TIMEOUT_MS = 8 * 60_000;

export type ReviewInput = {
  planMarkdown: string;
  goalContext: { goalId: string; tenantId: string; objective: string };
  locale?: "zh-CN" | "en-US";
  profileId: string;
  workspaceRoot: string;
  timeoutMs?: number;
  onBusy?: OnBusy;
};

export type ReviewOk = {
  ok: true;
  risks: string[];
  gaps: string[];
  alternatives: string[];
  score: number;
  model_version: string;
  cli_version: string;
  duration_ms: number;
  run_id: string;
  stdout_path: string;
};

export type ReviewErr = {
  ok: false;
  reason: AdvisorFailReason;
  duration_ms: number;
  run_id?: string;
};

export type ReviewDeps = {
  spawn?: (input: SpawnInput) => Promise<SpawnOk | SpawnErr>;
  singleFlight?: <T>(
    profileId: string,
    toolName: string,
    fn: () => Promise<T>,
    opts?: { onBusy?: OnBusy },
  ) => Promise<T | BusyResult>;
  readTextFile?: (path: string) => Promise<string>;
};

export async function reviewPlan(
  input: ReviewInput,
  deps: ReviewDeps = {},
): Promise<ReviewOk | ReviewErr> {
  const singleFlight = deps.singleFlight ?? defaultSingleFlight;
  try {
    const result = await singleFlight(
      input.profileId,
      TOOL_NAME,
      () => runReview(input, deps),
      { onBusy: input.onBusy },
    );
    return result as ReviewOk | ReviewErr;
  } catch {
    return { ok: false, reason: "cli_error", duration_ms: 0 };
  }
}

async function runReview(
  input: ReviewInput,
  deps: ReviewDeps,
): Promise<ReviewOk | ReviewErr> {
  const spawn = deps.spawn ?? defaultSpawn;
  const spawned = await spawn({
    cli: "claude",
    args: ["-p", systemPrompt(), "--output-format", "json", "--no-stream"],
    stdinJson: {
      planMarkdown: input.planMarkdown,
      goalContext: input.goalContext,
      locale: input.locale ?? "zh-CN",
    },
    timeoutMs: input.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    workspaceRoot: input.workspaceRoot,
    profileId: input.profileId,
    tool: TOOL_NAME,
  });

  if (!spawned.ok) {
    return {
      ok: false,
      reason: spawned.reason,
      duration_ms: spawned.durationMs,
      run_id: spawned.runId,
    };
  }

  try {
    const readTextFile = deps.readTextFile ?? Deno.readTextFile;
    const parsed = JSON.parse(await readTextFile(spawned.stdoutPath));
    const critique = parseCritique(parsed);
    if (!critique) {
      return {
        ok: false,
        reason: "parse_error",
        duration_ms: spawned.durationMs,
        run_id: spawned.runId,
      };
    }
    return {
      ok: true,
      ...critique,
      cli_version: "claude",
      duration_ms: spawned.durationMs,
      run_id: spawned.runId,
      stdout_path: spawned.stdoutPath,
    };
  } catch {
    return {
      ok: false,
      reason: "parse_error",
      duration_ms: spawned.durationMs,
      run_id: spawned.runId,
    };
  }
}

function parseCritique(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const row = value as Record<string, unknown>;
  if (
    !isStringArray(row.risks) ||
    !isStringArray(row.gaps) ||
    !isStringArray(row.alternatives) ||
    typeof row.score !== "number" ||
    row.score < 0 ||
    row.score > 100 ||
    typeof row.model_version !== "string"
  ) {
    return null;
  }
  return {
    risks: row.risks,
    gaps: row.gaps,
    alternatives: row.alternatives,
    score: row.score,
    model_version: row.model_version,
  };
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) &&
    value.every((item) => typeof item === "string");
}

function systemPrompt(): string {
  return [
    "You are a strict plan reviewer for FUXI Digital Director.",
    "Return JSON only.",
    'Schema: {"risks":string[],"gaps":string[],"alternatives":string[],"score":number,"model_version":string}.',
    "score must be an integer or number from 0 to 100.",
    "Do not include markdown fences or explanatory text.",
  ].join("\n");
}
