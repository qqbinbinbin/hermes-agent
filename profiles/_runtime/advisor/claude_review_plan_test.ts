import { assertEquals } from "jsr:@std/assert";
import { type ReviewDeps, reviewPlan } from "./claude_review_plan.ts";
import type { SpawnInput, SpawnOk } from "./runner.ts";

const baseInput = {
  planMarkdown: "# Plan\nDo the work",
  goalContext: {
    goalId: "goal-1",
    tenantId: "tenant-1",
    objective: "提升预算滚动预测能力",
  },
  profileId: "director@tenant-1",
  workspaceRoot: "/tmp/hermes-advisor",
} as const;

Deno.test("reviewPlan returns validated critique on happy path", async () => {
  const deps = depsFor(
    JSON.stringify({
      risks: ["风险"],
      gaps: ["缺口"],
      alternatives: ["替代方案"],
      score: 88,
      model_version: "claude-test",
    }),
  );

  const result = await reviewPlan(baseInput, deps);

  assertEquals(result.ok, true);
  if (!result.ok) throw new Error("expected ok");
  assertEquals(result.risks, ["风险"]);
  assertEquals(result.gaps, ["缺口"]);
  assertEquals(result.alternatives, ["替代方案"]);
  assertEquals(result.score, 88);
  assertEquals(result.model_version, "claude-test");
  assertEquals(result.cli_version, "claude");
  assertEquals(result.run_id, "run-1");
  assertEquals(result.stdout_path, "/tmp/stdout-happy.log");
});

Deno.test("reviewPlan uses default 8 minute timeout", async () => {
  let seen: SpawnInput | undefined;
  const deps = depsFor(validJson(), {
    onSpawnInput: (input) => {
      seen = input;
    },
  });

  await reviewPlan(baseInput, deps);

  assertEquals(seen?.timeoutMs, 480_000);
});

Deno.test("reviewPlan honors explicit timeout", async () => {
  let seen: SpawnInput | undefined;
  const deps = depsFor(validJson(), {
    onSpawnInput: (input) => {
      seen = input;
    },
  });

  await reviewPlan({ ...baseInput, timeoutMs: 1234 }, deps);

  assertEquals(seen?.timeoutMs, 1234);
});

Deno.test("reviewPlan maps spawn errors to ReviewErr", async () => {
  for (const reason of ["timeout", "cli_error", "cli_missing"] as const) {
    const result = await reviewPlan(baseInput, {
      spawn: async () => ({
        ok: false,
        reason,
        runId: "run-err",
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
      reason,
      duration_ms: 5,
      run_id: "run-err",
    });
  }
});

Deno.test("reviewPlan returns parse_error for non JSON stdout", async () => {
  const result = await reviewPlan(baseInput, depsFor("not-json"));
  assertEquals(result.ok, false);
  if (result.ok) throw new Error("expected error");
  assertEquals(result.reason, "parse_error");
});

Deno.test("reviewPlan returns parse_error for missing score", async () => {
  const result = await reviewPlan(
    baseInput,
    depsFor(JSON.stringify({
      risks: [],
      gaps: [],
      alternatives: [],
      model_version: "claude-test",
    })),
  );
  assertEquals(result.ok, false);
  if (result.ok) throw new Error("expected error");
  assertEquals(result.reason, "parse_error");
});

Deno.test("reviewPlan returns busy from reject single-flight", async () => {
  const result = await reviewPlan(baseInput, {
    spawn: async () => {
      throw new Error("spawn should not run");
    },
    singleFlight: async () => ({ ok: false, reason: "busy", duration_ms: 0 }),
    readTextFile: async () => "",
  });

  assertEquals(result, { ok: false, reason: "busy", duration_ms: 0 });
});

function validJson() {
  return JSON.stringify({
    risks: [],
    gaps: [],
    alternatives: [],
    score: 100,
    model_version: "claude-test",
  });
}

function depsFor(
  stdout: string,
  opts: { onSpawnInput?: (input: SpawnInput) => void } = {},
): ReviewDeps {
  const stdoutPath = opts.onSpawnInput
    ? `/tmp/stdout-${crypto.randomUUID()}.log`
    : "/tmp/stdout-happy.log";
  const ok: SpawnOk = {
    ok: true,
    runId: "run-1",
    stdoutPath,
    stderrPath: "/tmp/stderr.log",
    stdoutBytes: new TextEncoder().encode(stdout).byteLength,
    stderrBytes: 0,
    exitCode: 0,
    durationMs: 42,
  };
  return {
    spawn: async (input) => {
      opts.onSpawnInput?.(input);
      return ok;
    },
    singleFlight: (_profileId, _toolName, fn) => fn(),
    readTextFile: async (path) => {
      assertEquals(path, stdoutPath);
      return stdout;
    },
  };
}
