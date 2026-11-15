# CamCat Studio: localization extension

This project derives from CamCat at the commit recorded in `upstream.lock.json`.
The original multimodal retrieval, LangGraph editing workflow, Nova-derived UI,
versioned domain commands, optimistic locking, and compiled FFmpeg rendering are
the product foundation. Translation and dubbing extend that foundation.

## Implemented design

1. Preserve and validate the pinned baseline and its source attribution.
2. Add translation/dubbing as persistent jobs against an immutable session version.
3. Keep translations editable and apply results through normal versioned commands.
4. Register generated speech as session-scoped media, never licensed-library rows.
5. Render the new dialogue track through the existing compiler and renderer.
6. Verify the original editing path and the new localization path using real
   services, models, FFmpeg and the browser.

Initial scope: Chinese-to-English single-speaker narration, subtitle editing,
one configured English voice, per-cue preview, duration checks and MP4/SRT output.
Provider choices are explicit. Missing services fail with configuration errors.

## Storage and deployment

Source and documentation live in this repository. Dependencies, models, container
storage, logs, temporary files, browser reports and media live under
`/data/zjr/camcat_studio/`.

`scripts/studio.sh setup` installs the development dependencies there and creates
ignored compatibility links for the upstream Makefile. The host now runs the
project-specific rootless Docker stack with real local models; startup and
resource allocation are documented in [本机部署](local-deployment.md).

Translation/dubbing run through CamCat's persistent queue and heartbeat/lease path.
Jobs snapshot the subtitles and source version, then explicit application uses the
normal domain-command CAS transaction, state patch and audit history. Generated
speech becomes a project dialogue track and never a library Asset/Segment or Milvus row.
Transient derivatives inherit the original four-hour retention policy.

The added UI tab provides terminology input, original/translated text, editable
timing, draft recovery, real progress, review/apply, per-cue playback and SRT downloads.
Audio is measured, limited to 1.2x speed and padded to each cue window. An optional
single duration rewrite requires review before application. Changed subtitles block
export until speech is regenerated. Source audio volume is preserved for retranslation.

Real speech and FFmpeg checks exercise the existing TimelineCompilerV2, RenderBuild,
renderer and output verifier. An observed output-frame-rate mismatch was corrected
with explicit CFR output and a new renderer implementation version.

See [运行说明](localization-operations.md), [sources and notices](localization-notices.md)
and [verification status](localization-verification.md). Real Docker Compose and
browser acceptance completed on 2026-11-11, followed by a one-minute edited
localization sample. Performance and failure-path checks are documented in
[性能与稳定性验收](performance-reliability.md).
