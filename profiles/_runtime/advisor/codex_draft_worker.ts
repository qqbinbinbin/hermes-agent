import { join, normalize, relative } from "jsr:@std/path";
import { spawn as defaultSpawn, type SpawnInput } from "./runner.ts";
import {
  type BusyResult,
  type OnBusy,
  singleFlight as defaultSingleFlight,
} from "./single_flight.ts";
import type { AdvisorFailReason, SpawnErr, SpawnOk } from "./types.ts";

const TOOL_NAME = "advisor.codex.draft_worker";
const DEFAULT_TIMEOUT_MS = 20 * 60_000;
const DEFAULT_MAX_BYTES = 4 * 1024 * 1024;

export type DraftInput = {
  capabilityGap: string;
  exemplars?: Array<{ workerId: string; profileYaml: string }>;
  targetTenant: string;
  workspaceRoot: string;
  profileId: string;
  timeoutMs?: number;
  maxBytes?: number;
  onBusy?: OnBusy;
};

export type DraftOk = {
  ok: true;
  draftDir: string;
  profileYamlPath: string;
  skillFiles: Array<{ path: string; bytes: number }>;
  summaryPath: string;
  totalBytes: number;
  cli_version: string;
  duration_ms: number;
  input_hash: string;
  output_hash: string;
  run_id: string;
};

export type DraftErr = {
  ok: false;
  reason: AdvisorFailReason;
  duration_ms: number;
  run_id?: string;
};

export type DraftDeps = {
  spawn?: (input: SpawnInput) => Promise<SpawnOk | SpawnErr>;
  singleFlight?: <T>(
    profileId: string,
    toolName: string,
    fn: () => Promise<T>,
    opts?: { onBusy?: OnBusy },
  ) => Promise<T | BusyResult>;
};

export async function draftWorker(
  input: DraftInput,
  deps: DraftDeps = {},
): Promise<DraftOk | DraftErr> {
  const singleFlight = deps.singleFlight ?? defaultSingleFlight;
  try {
    const result = await singleFlight(
      input.profileId,
      TOOL_NAME,
      () => runDraft(input, deps),
      { onBusy: input.onBusy },
    );
    return result as DraftOk | DraftErr;
  } catch {
    return { ok: false, reason: "cli_error", duration_ms: 0 };
  }
}

async function runDraft(
  input: DraftInput,
  deps: DraftDeps,
): Promise<DraftOk | DraftErr> {
  const draftDir = join(
    input.workspaceRoot,
    "drafts",
    `worker-${crypto.randomUUID()}`,
  );
  await Deno.mkdir(join(draftDir, "skills"), { recursive: true });

  const spawn = deps.spawn ?? defaultSpawn;
  const spawned = await spawn({
    cli: "codex",
    args: [
      "exec",
      "--json",
      "--quiet",
      "--workdir",
      draftDir,
      prompt(input),
    ],
    timeoutMs: input.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    workspaceRoot: input.workspaceRoot,
    profileId: input.profileId,
    tool: TOOL_NAME,
  });

  if (!spawned.ok) {
    return {
      ok: false,
      reason: spawned.reason,
      duration_ms: spawned.durationMs,
      run_id: spawned.runId,
    };
  }

  try {
    const artifacts = await collectArtifacts(draftDir);
    const maxBytes = input.maxBytes ?? DEFAULT_MAX_BYTES;
    if (artifacts.totalBytes > maxBytes) {
      await Deno.remove(draftDir, { recursive: true });
      return {
        ok: false,
        reason: "oversize",
        duration_ms: spawned.durationMs,
        run_id: spawned.runId,
      };
    }

    const inputHash = await sha256Hex(canonicalJson(input));
    const outputHash = await sha256Hex(artifacts.outputText);
    const summaryPath = join(draftDir, "summary.json");
    const summary = {
      input_hash: inputHash,
      output_hash: outputHash,
      cli_version: "codex",
      duration_ms: spawned.durationMs,
      capability_gap: input.capabilityGap,
      generated_files: [
        { path: artifacts.profileYamlPath, bytes: artifacts.profileYamlBytes },
        ...artifacts.skillFiles,
      ],
      run_id: spawned.runId,
    };
    await Deno.writeTextFile(
      summaryPath,
      JSON.stringify(summary, null, 2) + "\n",
    );

    return {
      ok: true,
      draftDir,
      profileYamlPath: artifacts.profileYamlPath,
      skillFiles: artifacts.skillFiles,
      summaryPath,
      totalBytes: artifacts.totalBytes,
      cli_version: "codex",
      duration_ms: spawned.durationMs,
      input_hash: inputHash,
      output_hash: outputHash,
      run_id: spawned.runId,
    };
  } catch {
    return {
      ok: false,
      reason: "cli_error",
      duration_ms: spawned.durationMs,
      run_id: spawned.runId,
    };
  }
}

async function collectArtifacts(draftDir: string) {
  const root = normalize(draftDir);
  await validateDraftTree(root);
  const profileYamlPath = assertInside(root, join(root, "profile.yaml"));
  const profileYaml = await Deno.readTextFile(profileYamlPath);
  const skillFiles: Array<{ path: string; bytes: number }> = [];
  const skillBodies: string[] = [];
  const skillsDir = assertInside(root, join(root, "skills"));

  try {
    for await (const entry of Deno.readDir(skillsDir)) {
      if (!entry.isFile || !entry.name.endsWith(".md")) continue;
      const path = assertInside(root, join(skillsDir, entry.name));
      const body = await Deno.readTextFile(path);
      skillFiles.push({
        path,
        bytes: new TextEncoder().encode(body).byteLength,
      });
      skillBodies.push(body);
    }
  } catch (error) {
    if (!(error instanceof Deno.errors.NotFound)) throw error;
  }

  const profileYamlBytes = new TextEncoder().encode(profileYaml).byteLength;
  const totalBytes = profileYamlBytes +
    skillFiles.reduce((sum, file) => sum + file.bytes, 0);
  return {
    profileYamlPath,
    profileYamlBytes,
    skillFiles,
    totalBytes,
    outputText: profileYaml + skillBodies.join(""),
  };
}

async function validateDraftTree(root: string): Promise<void> {
  for await (const entry of Deno.readDir(root)) {
    if (entry.name === "profile.yaml") continue;
    if (entry.name === "summary.json") continue;
    if (entry.name === "skills" && entry.isDirectory) continue;
    throw new Error("path_escape");
  }

  const skillsDir = join(root, "skills");
  try {
    for await (const entry of Deno.readDir(skillsDir)) {
      if (!entry.isFile || !entry.name.endsWith(".md")) {
        throw new Error("path_escape");
      }
    }
  } catch (error) {
    if (!(error instanceof Deno.errors.NotFound)) throw error;
  }
}

function assertInside(root: string, path: string): string {
  const normalized = normalize(path);
  const rel = relative(root, normalized);
  if (rel === ".." || rel.startsWith("../") || rel.startsWith("..\\")) {
    if (normalized !== root) throw new Error("path_escape");
  }
  return normalized;
}

function prompt(input: DraftInput): string {
  return [
    "Draft a FUXI digital worker profile and skill skeleton.",
    "Write profile.yaml and skills/*.md inside the provided workdir only.",
    `Tenant: ${input.targetTenant}`,
    `Capability gap: ${input.capabilityGap}`,
    `Exemplars: ${JSON.stringify(input.exemplars ?? [])}`,
  ].join("\n");
}

function canonicalJson(value: unknown): string {
  return JSON.stringify(sortValue(value));
}

function sortValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortValue);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => [key, sortValue(item)]),
  );
}

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
