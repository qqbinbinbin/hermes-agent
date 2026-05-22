export type AdvisorFailReason =
  | "timeout"
  | "cli_error"
  | "cli_missing"
  | "parse_error"
  | "oversize"
  | "busy"
  | "killed_by_operator"
  | "foreground_blocked";

export type RunDir = {
  runId: string;
  dir: string;
  stdoutPath: string;
  stderrPath: string;
  heartbeatPath: string;
  metaPath: string;
};

export type SpawnInput = {
  cli: string;
  args: string[];
  stdinJson?: unknown;
  timeoutMs?: number;
  cwd?: string;
  workspaceRoot: string;
  profileId: string;
  tool: string;
  envDenylist?: string[];
  onHeartbeat?: (hb: { rss_kb: number; stdout_bytes: number }) => void;
  heartbeatIntervalMs?: number;
  onRunStart?: (run: RunDir) => void;
};

export type SpawnOk = {
  ok: true;
  runId: string;
  stdoutPath: string;
  stderrPath: string;
  stdoutBytes: number;
  stderrBytes: number;
  exitCode: 0;
  durationMs: number;
};

export type SpawnErr = {
  ok: false;
  reason: AdvisorFailReason;
  runId: string;
  stdoutPath: string;
  stderrPath: string;
  exitCode: number | null;
  durationMs: number;
};
