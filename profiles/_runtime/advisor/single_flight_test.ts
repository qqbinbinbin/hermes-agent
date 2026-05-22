import { assertEquals } from "jsr:@std/assert";
import { singleFlight } from "./single_flight.ts";

Deno.test("singleFlight rejects concurrent run when onBusy is reject", async () => {
  let release!: () => void;
  const first = singleFlight(
    "profile-a",
    "tool-a",
    () =>
      new Promise<string>((resolve) => {
        release = () => resolve("first");
      }),
    { onBusy: "reject" },
  );

  const second = await singleFlight(
    "profile-a",
    "tool-a",
    async () => "second",
    { onBusy: "reject" },
  );

  assertEquals(second, { ok: false, reason: "busy", duration_ms: 0 });
  release();
  assertEquals(await first, "first");
});

Deno.test("singleFlight queues concurrent run by default", async () => {
  const order: string[] = [];
  let release!: () => void;
  const first = singleFlight(
    "profile-b",
    "tool-b",
    () =>
      new Promise<string>((resolve) => {
        order.push("first-start");
        release = () => {
          order.push("first-end");
          resolve("first");
        };
      }),
  );

  const second = singleFlight("profile-b", "tool-b", async () => {
    order.push("second");
    return "second";
  });

  await new Promise((resolve) => setTimeout(resolve, 10));
  assertEquals(order, ["first-start"]);
  release();
  assertEquals(await first, "first");
  assertEquals(await second, "second");
  assertEquals(order, ["first-start", "first-end", "second"]);
});
