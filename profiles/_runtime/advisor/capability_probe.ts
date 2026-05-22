export type Capabilities = {
  claude_code: boolean;
  codex: boolean;
  versions: { claude_code?: string; codex?: string };
  probed_at: string;
};

export function parseVersion(text: string): string | undefined {
  return text.split(/\r?\n/, 1)[0]?.match(/v?(\d+\.\d+\.\d+)/)?.[1];
}

export async function detect(
  opts: { perCliTimeoutMs?: number } = {},
): Promise<Capabilities> {
  const timeoutMs = Math.min(opts.perCliTimeoutMs ?? 10_000, 30_000);
  const [claude, codex] = await Promise.all([
    probe("claude", timeoutMs),
    probe("codex", timeoutMs),
  ]);

  const versions: Capabilities["versions"] = {};
  if (claude.version) versions.claude_code = claude.version;
  if (codex.version) versions.codex = codex.version;

  return {
    claude_code: claude.available,
    codex: codex.available,
    versions,
    probed_at: new Date().toISOString(),
  };
}

async function probe(
  cli: string,
  timeoutMs: number,
): Promise<{ available: boolean; version?: string }> {
  const command = new Deno.Command(cli, {
    args: ["--version"],
    stdout: "piped",
    stderr: "null",
    env: Deno.env.toObject(),
    signal: AbortSignal.timeout(timeoutMs),
  });

  try {
    const output = await command.output();
    if (!output.success) return { available: false };
    const stdout = new TextDecoder().decode(output.stdout);
    return { available: true, version: parseVersion(stdout) };
  } catch {
    return { available: false };
  }
}
