import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const sourceUrl = new URL("../../extension/background.js", import.meta.url);

test("background correlates probe results, omits token from storage, and invalidates stale lessons", async () => {
  let clientListener;
  let nativeListener;
  let disconnectListener;
  let posted;
  let stored = {};
  const port = {
    onMessage: { addListener(callback) { nativeListener = callback; } },
    onDisconnect: { addListener(callback) { disconnectListener = callback; } },
    postMessage(message) { posted = message; },
  };
  const context = {
    structuredClone,
    crypto: { randomUUID: () => "request-1" },
    chrome: {
      storage: {
        session: {
          async get() { return stored; },
          async set(value) { stored = { ...stored, ...value }; },
        },
      },
      runtime: {
        connectNative(name) {
          assert.equal(name, "io.eduonline.ytdlp");
          return port;
        },
        onMessage: { addListener(callback) { clientListener = callback; } },
        sendMessage: async () => undefined,
      },
    },
  };
  context.globalThis = context;
  vm.runInNewContext(await readFile(sourceUrl, "utf8"), context);

  const probeResponse = new Promise((resolve) => {
    clientListener({
      type: "probe",
      tabId: 7,
      lessonUrl: "https://school.eduonline.io/learn/one/theory",
      discoveryFingerprint: "2:example",
      candidates: ["https://v.accelsite.io/v/ExamplePlayerId123456"],
    }, {}, resolve);
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  nativeListener({
    version: 2,
    requestId: posted.requestId,
    event: "result",
    payload: { videos: [
      {
        videoId: "stream-one",
        probeToken: "opaque-secret-token-one",
        title: "Lesson one",
        formats: [{ choiceId: "best", label: "Best", height: 720, audioOnly: false }],
      },
      {
        videoId: "stream-two",
        probeToken: "opaque-secret-token-two",
        title: "Lesson two",
        formats: [{ choiceId: "audio-only", label: "Audio", height: null, audioOnly: true }],
      },
    ] },
  });
  const response = await probeResponse;

  assert.equal(response.ok, true);
  assert.equal(posted.version, 2);
  assert.equal(JSON.stringify(stored).includes("opaque-secret-token"), false);
  assert.deepEqual(response.state.videos.map((video) => video.videoId), ["stream-one", "stream-two"]);
  assert.equal(response.state.selectedVideoId, "stream-one");
  assert.equal(context.EduonlineBackground.shouldRefreshPartialProbe(response.state), false);
  assert.equal(context.EduonlineBackground.shouldRefreshPartialProbe({
    phase: "ready",
    videos: [response.state.videos[0]],
    probeSummary: {
      candidateCount: 2,
      duplicateMediaCount: 0,
      failures: { INVALID_PROVIDER_CONFIG: 1 },
    },
  }), true);
  assert.equal(context.EduonlineBackground.shouldRefreshPartialProbe({
    phase: "ready",
    videos: [response.state.videos[0]],
    probeSummary: {
      candidateCount: 2,
      duplicateMediaCount: 1,
      failures: {},
    },
  }), false);
  const selected = await context.EduonlineBackground.handleClientMessage({
    type: "select-video",
    tabId: 7,
    lessonUrl: "https://school.eduonline.io/learn/one/theory",
    discoveryFingerprint: "2:example",
    videoId: "stream-two",
  });
  assert.equal(selected.selectedVideoId, "stream-two");

  const downloadPromise = context.EduonlineBackground.handleClientMessage({
    type: "download",
    tabId: 7,
    lessonUrl: "https://school.eduonline.io/learn/one/theory",
    discoveryFingerprint: "2:example",
    videoId: "stream-two",
    choiceId: "audio-only",
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(posted.payload.probeToken, "opaque-secret-token-two");
  assert.equal(posted.payload.choiceId, "audio-only");
  const restored = await context.EduonlineBackground.handleClientMessage({
    type: "get-state",
    tabId: 7,
    lessonUrl: "https://school.eduonline.io/learn/one/theory",
    discoveryFingerprint: "2:example",
  });
  assert.equal(restored.phase, "ready");
  const changedFrames = await context.EduonlineBackground.handleClientMessage({
    type: "get-state",
    tabId: 7,
    lessonUrl: "https://school.eduonline.io/learn/one/theory",
    discoveryFingerprint: "1:old",
  });
  assert.equal(changedFrames.phase, "idle");
  const stale = await context.EduonlineBackground.handleClientMessage({
    type: "get-state",
    tabId: 7,
    lessonUrl: "https://school.eduonline.io/learn/two/theory",
    discoveryFingerprint: "2:example",
  });
  assert.equal(stale.phase, "idle");

  disconnectListener();
  await assert.rejects(downloadPromise);
  assert.equal(context.EduonlineBackground.getState().error.code, "NATIVE_HOST_UNAVAILABLE");
  context.EduonlineBackground.handleNativeMessage({ version: 1, requestId: "old-host" });
  assert.equal(context.EduonlineBackground.getState().error.code, "UNSUPPORTED_VERSION");
});
