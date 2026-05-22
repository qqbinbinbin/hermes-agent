import { spawn as defaultSpawn, type SpawnInput } from "./runner.ts";
import {
  type BusyResult,
  type OnBusy,
  singleFlight as defaultSingleFlight,
} from "./single_flight.ts";
import type { AdvisorFailReason, SpawnErr, SpawnOk } from "./types.ts";

const TOOL_NAME = "advisor.codex.sandbox_refactor";
const DEFAULT_TIMEOUT_MS = 30 * 60_000;
const DEFAULT_MAX_DIFF_BYTES = 1024 * 1024;

export type SandboxInput = {
  workerId: string;
  currentProfileYaml: string;
  telemetrySummary: { window: "7d" | "30d"; metrics: Record<string, number> };
  callContext: { source: "cron:sandbox_pass" };
  profileId: string;
  workspaceRoot: string;
  timeoutMs?: number;
  maxDiffBytes?: number;
  onBusy?: OnBusy;
};

export type SandboxOk = {
  ok: true;
  proposedDiff: string;
  expectedGain: string;
  risks: string[];
  is_shadow: true;
  proposed_diff_hash: string;
  cli_version: string;
  duration_ms: number;
  run_id: string;
};

export type SandboxErr = {
  ok: false;
  reason: AdvisorFailReason;
  duration_ms: number;
  run_id?: string;
};

export type SandboxDeps = {
  spawn?: (input: SpawnInput) => Promise<SpawnOk | SpawnErr>;
  singleFlight?: <T>(
    profileId: string,
    toolName: string,
    fn: () => Promise<T>,
    opts?: { onBusy?: OnBusy },
  ) => Promise<T | BusyResult>;
  readTextFile?: (path: string) => Promise<string>;
};

export async function sandboxRefactor(
  input: SandboxInput,
  deps: SandboxDeps = {},
): Promise<SandboxOk | SandboxErr> {
  if (input.callContext.source !== "cron:sandbox_pass") {
    return { ok: false, reason: "foreground_blocked", duration_ms: 0 };
  }

  const singleFlight = deps.singleFlight ?? defaultSingleFlight;
  try {
    const result = await singleFlight(
      input.profileId,
      TOOL_NAME,
      () => runSandbox(input, deps),
      { onBusy: input.onBusy },
    );
    return result as SandboxOk | SandboxErr;
  } catch {
    return { ok: false, reason: "cli_error", duration_ms: 0 };
  }
}

async function runSandbox(
  input: SandboxInput,
  deps: SandboxDeps,
): Promise<SandboxOk | SandboxErr> {
  const spawn = deps.spawn ?? defaultSpawn;
  const spawned = await spawn({
    cli: "codex",
    args: [
      "exec",
      "--json",
      "--quiet",
      prompt(input),
    ],
    stdinJson: {
      workerId: input.workerId,
      currentProfileYaml: input.currentProfileYaml,
      telemetrySummary: input.telemetrySummary,
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
    const proposal = parseProposal(parsed);
    if (!proposal) {
      return parseError(spawned);
    }
    const diffBytes =
      new TextEncoder().encode(proposal.proposedDiff).byteLength;
    if (diffBytes > (input.maxDiffBytes ?? DEFAULT_MAX_DIFF_BYTES)) {
      return {
        ok: false,
        reason: "oversize",
        duration_ms: spawned.durationMs,
        run_id: spawned.runId,
      };
    }
    return {
      ok: true,
      ...proposal,
      is_shadow: true,
      proposed_diff_hash: await sha256Hex(proposal.proposedDiff),
      cli_version: "codex",
      duration_ms: spawned.durationMs,
      run_id: spawned.runId,
    };
  } catch {
    return parseError(spawned);
  }
}

function parseProposal(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const row = value as Record<string, unknown>;
  if (
    typeof row.proposedDiff !== "string" ||
    typeof row.expectedGain !== "string" ||
    !Array.isArray(row.risks) ||
    !row.risks.every((item) => typeof item === "string")
  ) {
    return null;
  }
  return {
    proposedDiff: row.proposedDiff,
    expectedGain: row.expectedGain,
    risks: row.risks as string[],
  };
}

function parseError(spawned: SpawnOk): SandboxErr {
  return {
    ok: false,
    reason: "parse_error",
    duration_ms: spawned.durationMs,
    run_id: spawned.runId,
  };
}

function prompt(input: SandboxInput): string {
  return [
    "Produce a shadow-only unified diff proposal for this worker profile.",
    "Return JSON only: { proposedDiff:string, expectedGain:string, risks:string[] }.",
    "Do not apply changes. Do not write files. This is a sandbox proposal only.",
    `Worker: ${input.workerId}`,
  ].join("\n");
}

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
