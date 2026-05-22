import { assert, assertEquals } from "jsr:@std/assert";
import { join } from "jsr:@std/path";
import { cancel, spawn } from "./runner.ts";

Deno.test({
  name: "spawn streams stdout and stderr to files",
  permissions: { run: true, read: true, write: true, env: true },
  async fn() {
    const workspaceRoot = await Deno.makeTempDir();
    const result = await spawn({
      cli: "/bin/sh",
      args: ["-c", "printf 'hello\\n'; printf 'warn\\n' >&2"],
      workspaceRoot,
      profileId: "director@tenant",
      tool: "advisor.test",
    });

    assertEquals(result.ok, true);
    if (!result.ok) throw new Error("expected spawn ok");
    assertEquals(await Deno.readTextFile(result.stdoutPath), "hello\n");
    assertEquals(await Deno.readTextFile(result.stderrPath), "warn\n");
    assertEquals(result.stdoutBytes, 6);
    assertEquals(result.stderrBytes, 5);
  },
});

Deno.test({
  name: "spawn returns cli_missing for missing executable",
  permissions: { run: true, read: true, write: true, env: true },
  async fn() {
    const workspaceRoot = await Deno.makeTempDir();
    const result = await spawn({
      cli: "definitely-missing-hermes-advisor-cli",
      args: [],
      workspaceRoot,
      profileId: "director@tenant",
      tool: "advisor.test",
    });

    assertEquals(result.ok, false);
    if (result.ok) throw new Error("expected spawn error");
    assertEquals(result.reason, "cli_missing");
  },
});

Deno.test({
  name: "spawn inherits env by default and applies denylist",
  permissions: { run: true, read: true, write: true, env: true },
  async fn() {
    const workspaceRoot = await Deno.makeTempDir();
    Deno.env.set("HTTPS_PROXY", "xyz");
    const inherited = await spawn({
      cli: "/bin/sh",
      args: ["-c", "printf '%s\\n' \"${HTTPS_PROXY:-missing}\""],
      workspaceRoot,
      profileId: "director@tenant",
      tool: "advisor.test",
    });
    assertEquals(await Deno.readTextFile(inherited.stdoutPath), "xyz\n");

    const denied = await spawn({
      cli: "/bin/sh",
      args: ["-c", "printf '%s\\n' \"${HTTPS_PROXY:-missing}\""],
      workspaceRoot,
      profileId: "director@tenant",
      tool: "advisor.test",
      envDenylist: ["HTTPS_PROXY"],
    });
    assertEquals(await Deno.readTextFile(denied.stdoutPath), "missing\n");
  },
});

Deno.test({
  name: "spawn enforces timeout and writes heartbeat",
  permissions: { run: true, read: true, write: true, env: true },
  async fn() {
    const workspaceRoot = await Deno.makeTempDir();
    const result = await spawn({
      cli: "/bin/sh",
      args: ["-c", "exec sleep 5"],
      workspaceRoot,
      profileId: "director@tenant",
      tool: "advisor.test",
      timeoutMs: 200,
      heartbeatIntervalMs: 20,
    });

    assertEquals(result.ok, false);
    if (result.ok) throw new Error("expected spawn error");
    assertEquals(result.reason, "timeout");
    assert(result.durationMs < 5000);
    const heartbeat = await Deno.readTextFile(
      join(workspaceRoot, ".advisor-runs", result.runId, "heartbeat.jsonl"),
    );
    assert(heartbeat.trim().length > 0);
  },
});

Deno.test({
  name: "cancel terminates a running process",
  permissions: { run: true, read: true, write: true, env: true },
  async fn() {
    const workspaceRoot = await Deno.makeTempDir();
    const started: string[] = [];
    const pending = spawn({
      cli: "/bin/sh",
      args: ["-c", "exec sleep 5"],
      workspaceRoot,
      profileId: "director@tenant",
      tool: "advisor.test",
      onRunStart: (run) => started.push(run.runId),
    });

    while (started.length === 0) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    assertEquals(await cancel(started[0]), true);
    const result = await pending;
    assertEquals(result.ok, false);
    if (result.ok) throw new Error("expected spawn error");
    assertEquals(result.reason, "killed_by_operator");
    assert(result.durationMs < 5000);
  },
});
