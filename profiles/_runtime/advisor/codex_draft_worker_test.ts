import { assert, assertEquals } from "jsr:@std/assert";
import { join } from "jsr:@std/path";
import { type DraftDeps, draftWorker } from "./codex_draft_worker.ts";
import type { SpawnInput } from "./runner.ts";

const baseInput = {
  capabilityGap: "需要一个预算滚动预测数智员工",
  targetTenant: "tenant-1",
  workspaceRoot: "",
  profileId: "director@tenant-1",
} as const;

Deno.test({
  name: "draftWorker returns DraftOk and writes summary manifest",
  permissions: { read: true, write: true },
  async fn() {
    const workspaceRoot = await Deno.makeTempDir();
    const result = await draftWorker(
      { ...baseInput, workspaceRoot },
      depsThatWritesDraft(),
    );

    assertEquals(result.ok, true);
    if (!result.ok) throw new Error("expected ok");
    assert(result.draftDir.startsWith(join(workspaceRoot, "drafts")));
    assertEquals(
      await Deno.readTextFile(result.profileYamlPath),
      "name: Budget Worker\n",
    );
    assertEquals(result.skillFiles.length, 1);
    assertEquals(result.run_id, "run-1");
    assert(result.output_hash.length === 64);

    const summary = JSON.parse(await Deno.readTextFile(result.summaryPath));
    assertEquals(summary.run_id, "run-1");
    assertEquals(summary.capability_gap, baseInput.capabilityGap);
    assertEquals(summary.input_hash, result.input_hash);
    assertEquals(summary.output_hash, result.output_hash);
    assertEquals(summary.generated_files.length, 2);
  },
});

Deno.test({
  name: "draftWorker uses default timeout and maxBytes",
  permissions: { read: true, write: true },
  async fn() {
    const workspaceRoot = await Deno.makeTempDir();
    let seen: SpawnInput | undefined;
    await draftWorker(
      { ...baseInput, workspaceRoot },
      depsThatWritesDraft({
        onSpawnInput: (input) => {
          seen = input;
        },
      }),
    );

    assertEquals(seen?.timeoutMs, 1_200_000);
    assertEquals(seen?.args.slice(0, 4), [
      "exec",
      "--json",
      "--quiet",
      "--workdir",
    ]);
  },
});

Deno.test({
  name: "draftWorker removes oversize draft directory",
  permissions: { read: true, write: true },
  async fn() {
    const workspaceRoot = await Deno.makeTempDir();
    const result = await draftWorker(
      { ...baseInput, workspaceRoot, maxBytes: 10 },
      depsThatWritesDraft(),
    );

    assertEquals(result.ok, false);
    if (result.ok) throw new Error("expected error");
    assertEquals(result.reason, "oversize");
    const drafts = [...Deno.readDirSync(join(workspaceRoot, "drafts"))];
    assertEquals(drafts.length, 0);
  },
});

Deno.test({
  name: "draftWorker maps cli_missing",
  permissions: { read: true, write: true },
  async fn() {
    const workspaceRoot = await Deno.makeTempDir();
    const result = await draftWorker(
      { ...baseInput, workspaceRoot },
      {
        spawn: async () => ({
          ok: false,
          reason: "cli_missing",
          runId: "run-missing",
          stdoutPath: "/tmp/stdout.log",
          stderrPath: "/tmp/stderr.log",
          exitCode: null,
          durationMs: 7,
        }),
        singleFlight: (_profileId, _toolName, fn) => fn(),
      },
    );

    assertEquals(result, {
      ok: false,
      reason: "cli_missing",
      duration_ms: 7,
      run_id: "run-missing",
    });
  },
});

Deno.test({
  name: "draftWorker rejects path escape artifacts",
  permissions: { read: true, write: true },
  async fn() {
    const workspaceRoot = await Deno.makeTempDir();
    const result = await draftWorker(
      { ...baseInput, workspaceRoot },
      depsThatWritesDraft({
        escape: true,
      }),
    );

    assertEquals(result.ok, false);
    if (result.ok) throw new Error("expected error");
    assertEquals(result.reason, "cli_error");
  },
});

Deno.test({
  name: "draftWorker returns busy from reject single-flight",
  async fn() {
    const result = await draftWorker(baseInput, {
      spawn: async () => {
        throw new Error("spawn should not run");
      },
      singleFlight: async () => ({ ok: false, reason: "busy", duration_ms: 0 }),
    });

    assertEquals(result, { ok: false, reason: "busy", duration_ms: 0 });
  },
});

function depsThatWritesDraft(
  opts: { onSpawnInput?: (input: SpawnInput) => void; escape?: boolean } = {},
): DraftDeps {
  return {
    spawn: async (input) => {
      opts.onSpawnInput?.(input);
      const workdir = input.args[input.args.indexOf("--workdir") + 1];
      await Deno.mkdir(join(workdir, "skills"), { recursive: true });
      await Deno.writeTextFile(
        join(workdir, "profile.yaml"),
        "name: Budget Worker\n",
      );
      await Deno.writeTextFile(
        join(workdir, "skills", "forecast.md"),
        "# Forecast\n",
      );
      if (opts.escape) {
        await Deno.writeTextFile(
          join(workdir, "skills", "../evil.yaml"),
          "evil\n",
        );
      }
      return {
        ok: true,
        runId: "run-1",
        stdoutPath: "/tmp/stdout.log",
        stderrPath: "/tmp/stderr.log",
        stdoutBytes: 0,
        stderrBytes: 0,
        exitCode: 0,
        durationMs: 42,
      };
    },
    singleFlight: (_profileId, _toolName, fn) => fn(),
  };
}
