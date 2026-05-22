import { assertEquals } from "jsr:@std/assert";
import { detect, parseVersion } from "./capability_probe.ts";

Deno.test("parseVersion extracts semver from first line", () => {
  assertEquals(parseVersion("codex-cli 1.2.3\nextra"), "1.2.3");
  assertEquals(parseVersion("Claude Code v0.11.4"), "0.11.4");
  assertEquals(parseVersion("no version"), undefined);
});

Deno.test({
  name: "detect returns false for missing CLIs without throwing",
  permissions: { run: true, env: true, read: true },
  async fn() {
    const previousPath = Deno.env.get("PATH");
    Deno.env.set("PATH", "/tmp/hermes-advisor-missing");
    try {
      const result = await detect({ perCliTimeoutMs: 50 });
      assertEquals(result.claude_code, false);
      assertEquals(result.codex, false);
      assertEquals(result.versions, {});
      assertEquals(typeof result.probed_at, "string");
    } finally {
      if (previousPath === undefined) Deno.env.delete("PATH");
      else Deno.env.set("PATH", previousPath);
    }
  },
});
