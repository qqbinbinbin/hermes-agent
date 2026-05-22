import { createRunDir } from "./run_dir.ts";
import type {
  AdvisorFailReason,
  RunDir,
  SpawnErr,
  SpawnInput,
  SpawnOk,
} from "./types.ts";

export type { SpawnErr, SpawnInput, SpawnOk } from "./types.ts";

type Running = {
  child: Deno.ChildProcess;
  killedByOperator: boolean;
};

const running = new Map<string, Running>();

export async function spawn(
  input: SpawnInput,
): Promise<SpawnOk | SpawnErr> {
  const run = await createRunDir(input.workspaceRoot, {
    cli: input.cli,
    args: input.args,
    profileId: input.profileId,
    tool: input.tool,
  });
  input.onRunStart?.(run);

  const startedAt = Date.now();
  let stdoutBytes = 0;
  let stderrBytes = 0;
  let timeoutId: number | undefined;
  let heartbeatId: number | undefined;
  let timeoutFired = false;
  let child: Deno.ChildProcess;

  try {
    const command = new Deno.Command(input.cli, {
      args: input.args,
      cwd: input.cwd,
      stdin: input.stdinJson === undefined ? "null" : "piped",
      stdout: "piped",
      stderr: "piped",
      env: childEnv(input.envDenylist),
      clearEnv: true,
    });
    child = command.spawn();
  } catch (error) {
    return errorResult(run, "cli_missing", null, startedAt);
  }

  running.set(run.runId, { child, killedByOperator: false });

  const stdoutTask = writeStream(child.stdout, run.stdoutPath, (bytes) => {
    stdoutBytes += bytes;
  });
  const stderrTask = writeStream(child.stderr, run.stderrPath, (bytes) => {
    stderrBytes += bytes;
  });

  if (input.stdinJson !== undefined && child.stdin) {
    const writer = child.stdin.getWriter();
    await writer.write(
      new TextEncoder().encode(JSON.stringify(input.stdinJson) + "\n"),
    );
    await writer.close();
  }

  const writeHeartbeat = async () => {
    const hb = {
      ts: new Date().toISOString(),
      pid: child.pid,
      rss_kb: 0,
      stdout_bytes: stdoutBytes,
      stderr_bytes: stderrBytes,
    };
    input.onHeartbeat?.({ rss_kb: hb.rss_kb, stdout_bytes: stdoutBytes });
    await Deno.writeTextFile(run.heartbeatPath, JSON.stringify(hb) + "\n", {
      append: true,
    }).catch(() => undefined);
  };
  await writeHeartbeat();
  heartbeatId = setInterval(
    () => void writeHeartbeat(),
    input.heartbeatIntervalMs ?? 30_000,
  );

  if (input.timeoutMs !== undefined) {
    timeoutId = setTimeout(() => {
      timeoutFired = true;
      terminate(child);
    }, input.timeoutMs);
  }

  const status = await child.status;
  await Promise.all([stdoutTask, stderrTask]);
  if (timeoutId !== undefined) clearTimeout(timeoutId);
  if (heartbeatId !== undefined) clearInterval(heartbeatId);
  const state = running.get(run.runId);
  running.delete(run.runId);

  if (state?.killedByOperator) {
    return errorResult(run, "killed_by_operator", status.code, startedAt);
  }
  if (timeoutFired) {
    return errorResult(run, "timeout", status.code, startedAt);
  }
  if (status.code !== 0) {
    return errorResult(run, "cli_error", status.code, startedAt);
  }

  return {
    ok: true,
    runId: run.runId,
    stdoutPath: run.stdoutPath,
    stderrPath: run.stderrPath,
    stdoutBytes,
    stderrBytes,
    exitCode: 0,
    durationMs: Date.now() - startedAt,
  };
}

export async function cancel(runId: string): Promise<boolean> {
  const state = running.get(runId);
  if (!state) return false;
  state.killedByOperator = true;
  await terminate(state.child);
  return true;
}

function childEnv(denylist: string[] = []): Record<string, string> {
  const env = Deno.env.toObject();
  for (const key of denylist) delete env[key];
  return env;
}

async function writeStream(
  stream: ReadableStream<Uint8Array>,
  path: string,
  onChunk: (bytes: number) => void,
): Promise<void> {
  const file = await Deno.open(path, {
    write: true,
    append: true,
    create: true,
  });
  try {
    for await (const chunk of stream) {
      onChunk(chunk.byteLength);
      await file.write(chunk);
    }
  } finally {
    file.close();
  }
}

async function terminate(child: Deno.ChildProcess): Promise<void> {
  try {
    child.kill("SIGTERM");
  } catch {
    return;
  }
  const done = child.status.then(() => true);
  let killTimer: number | undefined;
  const timeout = new Promise<boolean>((resolve) => {
    killTimer = setTimeout(() => resolve(false), 2_000);
  });
  const exited = await Promise.race([done, timeout]);
  if (killTimer !== undefined) clearTimeout(killTimer);
  if (!exited) {
    try {
      child.kill("SIGKILL");
    } catch {
      // already exited
    }
  }
}

function errorResult(
  run: RunDir,
  reason: AdvisorFailReason,
  exitCode: number | null,
  startedAt: number,
): SpawnErr {
  return {
    ok: false,
    reason,
    runId: run.runId,
    stdoutPath: run.stdoutPath,
    stderrPath: run.stderrPath,
    exitCode,
    durationMs: Date.now() - startedAt,
  };
}
