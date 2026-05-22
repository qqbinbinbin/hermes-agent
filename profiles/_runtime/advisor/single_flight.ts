export type OnBusy = "queue" | "reject";
export type BusyResult = { ok: false; reason: "busy"; duration_ms: 0 };

const queues = new Map<string, Promise<unknown>>();

export async function singleFlight<T>(
  profileId: string,
  toolName: string,
  fn: () => Promise<T>,
  opts: { onBusy?: OnBusy } = {},
): Promise<T | BusyResult> {
  const key = `${profileId}::${toolName}`;
  const current = queues.get(key);
  if (current && opts.onBusy === "reject") {
    return { ok: false, reason: "busy", duration_ms: 0 };
  }

  const run = async () => {
    if (current) await current.catch(() => undefined);
    return await fn();
  };
  const next = run();
  queues.set(key, next);
  try {
    return await next;
  } finally {
    if (queues.get(key) === next) queues.delete(key);
  }
}
