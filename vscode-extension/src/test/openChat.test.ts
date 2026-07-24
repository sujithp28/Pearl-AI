import assert from "node:assert/strict";
import { test } from "node:test";
import { openChat, PEARL_CONNECTED_MESSAGE } from "../commands/openChat";

test("openChat reports that Pearl is connected", () => {
  let received: string | undefined;

  openChat((message) => {
    received = message;
  });

  assert.strictEqual(received, PEARL_CONNECTED_MESSAGE);
});
