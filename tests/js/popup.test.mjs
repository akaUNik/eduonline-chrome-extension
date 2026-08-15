import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const sourceUrl = new URL("../../extension/popup.js", import.meta.url);

test("popup helpers recognize lessons and order quality choices", async () => {
  const context = { URL };
  context.globalThis = context;
  vm.runInNewContext(await readFile(sourceUrl, "utf8"), context);

  assert.equal(context.EduonlinePopup.supportedLesson("http://school.eduonline.io/learn/id/theory"), true);
  assert.equal(context.EduonlinePopup.supportedLesson("https://school.eduonline.io/catalog"), false);
  const formats = context.EduonlinePopup.sortFormats([
    { choiceId: "audio-only", audioOnly: true, height: null },
    { choiceId: "video-360", audioOnly: false, height: 360 },
    { choiceId: "best", audioOnly: false, height: 720 },
    { choiceId: "video-720", audioOnly: false, height: 720 },
  ]);
  assert.deepEqual([...formats.map((format) => format.choiceId)], [
    "best",
    "video-720",
    "video-360",
    "audio-only",
  ]);
  assert.equal(context.EduonlinePopup.formatDuration(2092), "34:52");
  const state = {
    selectedVideoId: "two",
    videos: [
      { videoId: "one", title: "First", formats: [{ choiceId: "video-360" }] },
      { videoId: "two", title: "Second", formats: [{ choiceId: "video-720" }] },
    ],
  };
  assert.equal(context.EduonlinePopup.selectedVideo(state).title, "Second");
  assert.equal(context.EduonlinePopup.selectedVideo({ videos: [state.videos[0]] }).title, "First");
  assert.equal(
    context.EduonlinePopup.probeSummaryText({
      videos: [state.videos[0]],
      probeSummary: {
        candidateCount: 2,
        duplicatePlayerCount: 0,
        duplicateMediaCount: 0,
        failures: { AUTHORIZATION_REQUIRED: 1 },
        failureStages: { "PLAYER_FETCH:AUTHORIZATION_REQUIRED": 1 },
      },
    }),
    "1 of 2 player frames supported: 1 PLAYER_FETCH:AUTHORIZATION_REQUIRED",
  );
  assert.equal(
    context.EduonlinePopup.friendlyConnectionError(
      new Error("Could not establish connection. Receiving end does not exist."),
    ).code,
    "CONTENT_SCRIPT_UNAVAILABLE",
  );
});
