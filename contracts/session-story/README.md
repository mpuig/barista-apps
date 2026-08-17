# contracts/session-story

Schemas for portable session knowledge and semantic transfer.

- **Session Story** — `v1alpha1/schema.json`. A deterministic, redacted,
  **non-executable** knowledge bundle. Media type
  `application/vnd.barista.session-story.v1alpha1+json`. Contains no capsule
  objects, writable filesystem, bearer grants, or executable permission. The
  `story_id` is a content digest over the canonical bundle bytes; a `removed`
  report names redaction categories and counts, never removed values.
- **Semantic State Bundle** — `v1alpha1/semantic-state.schema.json`. The
  adapter-produced continuation bundle used by semantic Lift. Media type
  `application/vnd.barista.semantic-state.v1alpha1+json`. Preserves
  harness-native state as **opaque** attachments with media types and carries
  an honest `fidelity` report; the host never normalizes it away.

Exact (memory) transfer uses kernel capsules, not these schemas. These are the
safe/semantic views: knowledge without executable state.

- **OpenSpec:** `openspec/changes/apps-001-portable-agent-apps/specs/session-story/spec.md`
