import { join } from "jsr:@std/path";
import type { RunDir } from "./types.ts";

export type { RunDir } from "./types.ts";

export async function createRunDir(
  workspaceRoot: string,
  ctx: { cli: string; tool: string; profileId: string; args: string[] },
): Promise<RunDir> {
  const runId = crypto.randomUUID();
  const dir = join(workspaceRoot, ".advisor-runs", runId);
  await Deno.mkdir(dir, { recursive: true });

  const run: RunDir = {
    runId,
    dir,
    stdoutPath: join(dir, "stdout.log"),
    stderrPath: join(dir, "stderr.log"),
    heartbeatPath: join(dir, "heartbeat.jsonl"),
    metaPath: join(dir, "meta.json"),
  };

  await Deno.writeTextFile(run.stdoutPath, "");
  await Deno.writeTextFile(run.stderrPath, "");
  await Deno.writeTextFile(run.heartbeatPath, "");
  await Deno.writeTextFile(
    run.metaPath,
    JSON.stringify(
      {
        runId,
        cli: ctx.cli,
        args: ctx.args,
        startedAt: new Date().toISOString(),
        profileId: ctx.profileId,
        tool: ctx.tool,
      },
      null,
      2,
    ) + "\n",
  );

  return run;
}
