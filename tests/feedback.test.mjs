import test from "node:test";
import assert from "node:assert/strict";
import {COMMENT_MAX, feedbackPath, nextVote, SHARE_NOTICE, usefulText, voteBody, voteStatus} from "../frontend/feedback.mjs";
import {toChatEntries} from "../frontend/history.mjs";

test("a saved answer has its vote endpoint; an unsaved one has none", () => {
  assert.equal(feedbackPath("c-1", 42), "/api/v1/conversations/c-1/messages/42/feedback");
  assert.equal(feedbackPath("a/b", 7), "/api/v1/conversations/a%2Fb/messages/7/feedback");
  assert.equal(feedbackPath(null, 42), null);
  assert.equal(feedbackPath("c-1", null), null);
  assert.equal(feedbackPath("c-1", 0), null);
});

test("clicking the same thumb again removes the vote", () => {
  assert.equal(nextVote(null, 1), 1);
  assert.equal(nextVote(1, 1), 0);
  assert.equal(nextVote(1, -1), -1);
  assert.equal(nextVote(-1, -1), 0);
});

test("only a thumbs down carries the comment", () => {
  assert.deepEqual(voteBody(1, "bien"), {value: 1});
  assert.deepEqual(voteBody(-1, "  no era eso  "), {value: -1, comment: "no era eso"});
  assert.deepEqual(voteBody(-1, "   "), {value: -1});
  assert.equal(voteBody(-1, "x".repeat(900)).comment.length, COMMENT_MAX);
});

test("the page says the thumbs down is shared before sending it", () => {
  assert.match(SHARE_NOTICE, /se comparten con el administrador/);
  assert.equal(voteStatus(0), "");
  assert.match(voteStatus(-1), /revisaremos/);
});

test("the admin card shows the useful percentage", () => {
  assert.equal(usefulText({up: 17, down: 3, total: 20, useful_percent: 85}), "85 % útiles (17 de 20)");
  assert.equal(usefulText({up: 0, down: 0, total: 0, useful_percent: null}), "Todavía no hay calificaciones.");
});

test("a saved conversation keeps each answer's id and vote", () => {
  assert.deepEqual(toChatEntries([
    {id: 1, role: "user", content: "¿Qué es una VPN?"},
    {id: 2, role: "assistant", content: "Un túnel cifrado.", route: "general", sources: [], feedback: -1},
    {id: 4, role: "assistant", content: "Otra.", route: "general", sources: []},
  ]), [
    {role: "user", text: "¿Qué es una VPN?"},
    {role: "assistant", text: "Un túnel cifrado.", status: "general", sources: [], messageId: 2, vote: -1},
    {role: "assistant", text: "Otra.", status: "general", sources: [], messageId: 4, vote: null},
  ]);
});
