# ADR-0005: Prompts are versioned source; client overlays are deploy-time config

**Status:** Accepted · **Date:** 2026-06-12

## Context

The voice assistant's behavior is defined almost entirely by a prompt, and the
project is about to grow more of them (the LLM-backed extraction upgrade of
ADR-0002 is first in line). That raises the question of where prompt text
lives — in particular, whether it belongs in a public repo at all.

The instinct to keep prompts out of the repo treats them like credentials.
They aren't: a prompt leaking costs nothing, while a prompt *drifting
unversioned* costs real debugging time. Iterating the showing-feedback
assistant against live test calls made this concrete — five calls, each
exposing a behavior bug (a rigid question script, a narrated end-call tool, a
farewell that never triggered the hangup, a doubled farewell), each fixed by
a prompt change that needed review, history, and a place to live. Treating
the prompt as code is what made that loop converge.

What genuinely must stay out of a public repo is the same thing that always
must: anything identifying who a deployment serves — brokerage names and
branding language, market-specific copy, pronunciation dictionaries full of
local street and neighborhood names.

## Decision

One rule, three destinations:

1. **Generic prompt → the repo, next to its consumer.** The behavior-defining
   template is versioned source: the voice assistant's config lives in
   `integrations/vapi/.../assistants/`, and future LLM-node prompts live
   beside the service that calls them (a prompt and its output schema must
   version together). Prompt changes are commits — reviewed, dated, and
   justified like any other code change, ideally pinned by golden-transcript
   or golden-output tests.
2. **Client overlay → instantiation-time config, never committed.** A live
   assistant (or a deployed LLM node) is built *from* the template, then
   customized per client: voice selection, greeting branding, pronunciation
   entries. The live platform copy (e.g. the assistant object in the voice
   provider's account) is the runtime artifact, like a deployed image —
   generic learnings discovered while iterating it are backported to the
   template; client-specific deltas are not.
3. **Keys → Secret Manager / `.env`**, exactly like every other credential
   (ADR and module wiring already exist). Prompts never carry secrets, so
   they never need that channel.

## Consequences

- (+) Prompt behavior has the same audit trail as code: what changed, when,
  and why — with live-call evidence in the commit history.
- (+) The public repo demonstrates prompt craft without exposing any client
  context; the scrub boundary stays exactly where it already was.
- (+) Zero new infrastructure: templates ship with the code, overlays ride
  the existing per-client deploy path, keys ride the existing secret path.
- (−) Template ↔ live-copy drift is real: iterating a live assistant means
  remembering to backport. Acceptable while assistants are few; a sync/diff
  script is the cheap remedy when it starts to hurt.
- (−) Prompt edits require a commit and deploy, which is the point — but if
  non-engineers ever need to edit prompts, or A/B versions need to run
  concurrently, that is the revisit trigger for a managed prompt store
  (e.g. the tracing platform's prompt hub).
