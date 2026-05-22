import { assert, assertEquals } from "jsr:@std/assert";
import { type SandboxDeps, sandboxRefactor } from "./codex_sandbox_refactor.ts";
import type { SpawnInput } from "./runner.ts";

const baseInput = {
  workerId: "worker-1",
  currentProfileYaml: "name: Budget Worker\n",
  telemetrySummary: { window: "7d", metrics: { success_rate: 0.91 } },
  callContext: { source: "cron:sandbox_pass" },
  profileId: "director@tenant-1",
  workspaceRoot: "/tmp/hermes-advisor",
} as const;

Deno.test("sandboxRefactor blocks foreground context", async () => {
  const result = await sandboxRefactor({
    ...baseInput,
    callContext: { source: "foreground" } as never,
  });

  assertEquals(result, {
    ok: false,
    reason: "foreground_blocked",
    duration_ms: 0,
  });
});

Deno.test("sandboxRefactor uses default timeout and maxDiffBytes", async () => {
  let seen: SpawnInput | undefined;
  await sandboxRefactor(
    baseInput,
    depsFor(validJson(), {
      onSpawnInput: (input) => {
        seen = input;
      },
    }),
  );

  assertEquals(seen?.timeoutMs, 1_800_000);
});

Deno.test("sandboxRefactor returns oversize for too-large diff", async () => {
  const result = await sandboxRefactor(
    { ...baseInput, maxDiffBytes: 10 },
    depsFor(validJson({ proposedDiff: "x".repeat(11) })),
  );

  assertEquals(result.ok, false);
  if (result.ok) throw new Error("expected error");
  assertEquals(result.reason, "oversize");
});

Deno.test("sandboxRefactor returns shadow sandbox proposal", async () => {
  const result = await sandboxRefactor(baseInput, depsFor(validJson()));

  assertEquals(result.ok, true);
  if (!result.ok) throw new Error("expected ok");
  assertEquals(result.is_shadow, true);
  assertEquals(result.proposedDiff, "--- a/profile.yaml\n+++ b/profile.yaml\n");
  assertEquals(result.expectedGain, "提升稳定性");
  assertEquals(result.risks, ["需要人工复核"]);
  assert(result.proposed_diff_hash.length === 64);
  assertEquals(result.run_id, "run-1");
});

Deno.test("sandboxRefactor maps cli_missing", async () => {
  const result = await sandboxRefactor(baseInput, {
    spawn: async () => ({
      ok: false,
      reason: "cli_missing",
      runId: "run-missing",
      stdoutPath: "/tmp/stdout.log",
      stderrPath: "/tmp/stderr.log",
      exitCode: null,
      durationMs: 5,
    }),
    singleFlight: (_profileId, _toolName, fn) => fn(),
    readTextFile: async () => "",
  });

  assertEquals(result, {
    ok: false,
    reason: "cli_missing",
    duration_ms: 5,
    run_id: "run-missing",
  });
});

Deno.test("sandboxRefactor returns busy from reject single-flight", async () => {
  const result = await sandboxRefactor(baseInput, {
    spawn: async () => {
      throw new Error("spawn should not run");
    },
    singleFlight: async () => ({ ok: false, reason: "busy", duration_ms: 0 }),
    readTextFile: async () => "",
  });

  assertEquals(result, { ok: false, reason: "busy", duration_ms: 0 });
});

function validJson(
  overrides: Partial<
    { proposedDiff: string; expectedGain: string; risks: string[] }
  > = {},
) {
  return JSON.stringify({
    proposedDiff: "--- a/profile.yaml\n+++ b/profile.yaml\n",
    expectedGain: "提升稳定性",
    risks: ["需要人工复核"],
    ...overrides,
  });
}

function depsFor(
  stdout: string,
  opts: { onSpawnInput?: (input: SpawnInput) => void } = {},
): SandboxDeps {
  const stdoutPath = "/tmp/sandbox-stdout.log";
  return {
    spawn: async (input) => {
      opts.onSpawnInput?.(input);
      return {
        ok: true,
        runId: "run-1",
        stdoutPath,
        stderrPath: "/tmp/stderr.log",
        stdoutBytes: new TextEncoder().encode(stdout).byteLength,
        stderrBytes: 0,
        exitCode: 0,
        durationMs: 42,
      };
    },
    singleFlight: (_profileId, _toolName, fn) => fn(),
    readTextFile: async (path) => {
      assertEquals(path, stdoutPath);
      return stdout;
    },
  };
}
