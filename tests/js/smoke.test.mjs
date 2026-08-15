import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const contractUrl = new URL("../../protocol/v2.json", import.meta.url);

test("protocol contract is valid JSON", async () => {
  const contract = JSON.parse(await readFile(contractUrl, "utf8"));
  assert.equal(contract.version, 2);
  assert.ok(contract.actions.includes("download"));
});
