import { assert, assertEquals } from "jsr:@std/assert";
import { join } from "jsr:@std/path";
import { createRunDir } from "./run_dir.ts";

Deno.test({
  name: "createRunDir creates advisor run files and meta",
  permissions: { read: true, write: true },
  async fn() {
    const workspaceRoot = await Deno.makeTempDir();
    const run = await createRunDir(workspaceRoot, {
      cli: "codex",
      tool: "advisor.codex.draft_worker",
      profileId: "director@tenant",
      args: ["--json"],
    });

    assert(run.dir.startsWith(join(workspaceRoot, ".advisor-runs")));
    assert(run.stdoutPath.endsWith("stdout.log"));
    assert(run.stderrPath.endsWith("stderr.log"));
    assert(run.heartbeatPath.endsWith("heartbeat.jsonl"));

    const meta = JSON.parse(await Deno.readTextFile(run.metaPath));
    assertEquals(meta.runId, run.runId);
    assertEquals(meta.cli, "codex");
    assertEquals(meta.tool, "advisor.codex.draft_worker");
    assertEquals(meta.profileId, "director@tenant");
    assertEquals(meta.args, ["--json"]);
    assert(typeof meta.startedAt === "string");
  },
});
