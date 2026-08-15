import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const sourceUrl = new URL("../../extension/content.js", import.meta.url);

test("content script registers a discovery listener with mocked Chrome APIs", async () => {
  let listener;
  const context = {
    URL,
    chrome: {
      runtime: {
        onMessage: {
          addListener(callback) {
            listener = callback;
          },
        },
      },
    },
    document: { title: "Example lesson", querySelectorAll: () => [] },
    window: { location: { href: "https://school.eduonline.io/learn/example/theory" } },
  };
  context.globalThis = context;

  vm.runInNewContext(await readFile(sourceUrl, "utf8"), context);
  assert.equal(typeof listener, "function");

  let response;
  listener({ type: "collect-discovery" }, {}, (value) => {
    response = value;
  });

  assert.equal(response.pageTitle, "Example lesson");
  assert.deepEqual([...response.candidates], []);
});

test("content script accepts bounded AccelSite iframe candidates only", async () => {
  const frames = [
    { src: "https://v.accelsite.io/v/ExamplePlayerId123456?showTitle=true" },
    { src: "https://evil.example/v/ExamplePlayerId123456" },
    { src: "http://v.accelsite.io/v/ExamplePlayerId123456" },
    { src: "https://v.accelsite.io/v/ExamplePlayerId123456?token=secret" },
  ];
  const context = {
    URL,
    document: { title: "Example", querySelectorAll: () => frames },
    window: { location: { href: "https://school.eduonline.io/learn/example/theory" } },
  };
  context.globalThis = context;

  vm.runInNewContext(await readFile(sourceUrl, "utf8"), context);
  const result = context.EduonlineDiscovery.collectDiscovery();

  assert.deepEqual([...result.candidates], [frames[0].src]);
  assert.equal(context.EduonlineDiscovery.canonicalLessonUrl("https://example.com/learn/x"), null);
});

test("content script preserves two-video DOM order and removes duplicate candidates", async () => {
  const fixtureUrl = new URL("../fixtures/multi_video_iframes.json", import.meta.url);
  const frames = JSON.parse(await readFile(fixtureUrl, "utf8"));
  const context = {
    URL,
    document: { title: "Two videos", querySelectorAll: () => frames },
    window: { location: { href: "https://school.eduonline.io/learn/example/theory" } },
  };
  context.globalThis = context;

  vm.runInNewContext(await readFile(sourceUrl, "utf8"), context);
  const result = context.EduonlineDiscovery.collectDiscovery();

  assert.deepEqual([...result.candidates], [frames[0].src, frames[1].src]);
  assert.notEqual(
    result.candidateFingerprint,
    context.EduonlineDiscovery.candidateFingerprint([frames[0].src]),
  );
});
