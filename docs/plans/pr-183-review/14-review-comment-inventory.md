# PR #183 Review Plan 14: Later Review Batch

This inventory records the 104 pending review comments left by `Glinte` on
[PR 183](https://github.com/redstone-squid/Redstone-Squid/pull/183) for the inclusive commit range
[`2605367`](https://github.com/redstone-squid/Redstone-Squid/pull/183/commits/2605367013a2e88a29d6ecb1b742c7bffa822126)
through
[`aa85f68`](https://github.com/redstone-squid/Redstone-Squid/pull/183/commits/aa85f68f77f11616da29a0b4c276f83fe4aac059).

The CLI, web frontend, and Minecraft plugin are excluded by path: `cli/`, `web/`, and `minecraft/`.
Core backend files under `squid/` remain included even when their introducing commit has a `minecraft:` or
`minecraft-auth:` subject. Each comment appears once under its primary category. Comment wording, spelling, and
capitalization are preserved.

This file is the immutable source inventory. The implementation plan is split by ownership boundary rather than by
the reviewer's wording category; that prevents one architectural change from being planned independently in three
different files. Every inventory row is assigned to exactly one detailed subplan:

| Subplan | Primary ownership | Threads |
|---|---|---:|
| [14A](14a-platform-delivery-tooling.md) | Runtime composition, build delivery, CI, and cross-cutting test shape | 6 |
| [14B](14b-api-idempotency-rate-limits.md) | HTTP idempotency and rate limiting | 12 |
| [14C](14c-notifications.md) | Event materialization, inbox policy, and Discord notification UX | 13 |
| [14D](14d-submission-form-drafts.md) | Submission manifests and synchronized drafts | 17 |
| [14E](14e-submission-finalization-builds.md) | Durable finalization and canonical build creation | 20 |
| [14F](14f-media-processing-api.md) | Media upload, normalization, persistence, and worker registration | 11 |
| [14G](14g-schematics-persistence-publication.md) | Schematic storage and preview publication | 10 |
| [14H](14h-accounts-records-persistence.md) | Account identity and computed-record persistence | 9 |
| [14I](14i-minecraft-auth.md) | Minecraft installation and player authorization | 6 |
| **Total** |  | **104** |

## Triage rules

The audit base is the current branch after the completed plans 1–13, not the historical comment anchor. A thread is
classified in a subplan as:

- **Already addressed** when current production code, rather than only a test helper, implements the requested
  outcome. The disposition names the surviving code contract.
- **Retain** when the review proposed a change but the current design has a concrete invariant that would be lost.
  The plan records that invariant and adds or strengthens the proof where needed.
- **Fix** when repository work remains. The disposition points to a milestone and an acceptance test.
- **Defer** only when the work belongs to a named existing plan whose scope is broader than PR #183. A bare future
  TODO is not a disposition.

The detailed plans cover repository work only. Posting replies or resolving GitHub threads still requires separate
authorization. The 52 later comments called out in the directory README remain outside this plan's `aa85f68`
cutoff.

The exact-once crosswalk is an acceptance check: extract each `discussion_r...` ID from this inventory and from the
thread-disposition tables in 14A–14I, sort/unique both sets, and require identical 104-item sets with no duplicate in
either source. This check was run after the independent plan review; the owner/count table above is its readable
summary.

| Category | Count |
|---|---:|
| Architecture and separation of concerns | 25 |
| Naming, terminology, and documentation | 25 |
| Data modeling, typing, and persistence | 17 |
| Duplication, cleanup, and maintainability | 14 |
| Correctness, performance, and reliability | 9 |
| Tests and CI | 8 |
| User experience, API contracts, and i18n | 6 |
| **Total** | **104** |

## Architecture and separation of concerns

- [`squid/bootstrap.py`: “no a fan of this function injection”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796445586)
- [`squid/bot/errors.py`: “why does this need to be nested”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3789562468)
- [`squid/schematics/infrastructure/repository.py`: “wtf is this crap”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796495596)
- [`squid/schematics/infrastructure/repository.py`: “is this really a good design? actually unsure.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796530955)
- [`squid/api/v1/records.py`: “api level filtering is bad design”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790725586)
- [`squid/api/idempotency.py`: “don't like this tbh”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796563710)
- [`squid/api/idempotency.py`: “we REALLY should just be a middleware no?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796581124)
- [`squid/api/rate_limit.py`: “we probably should use a library”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790765273)
- [`squid/api/rate_limit.py`: “isn't an ASGI middleware better? what is the problem here?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790775846)
- [`squid/events/infrastructure/listener.py`: “whats the point”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790849505)
- [`squid/notifications/infrastructure/repository.py`: “not sure if I like deduplicating by the DomainEventRecord table”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790864864)
- [`squid/submissions/application/forms.py`: “use a contextvar, hopefully done in later commits?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790829849)
- [`squid/submissions/application/forms.py`: “why.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790834570)
- [`squid/api/v1/schemas/submissions.py`: “why are we not using Pydantic for this”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790911109)
- [`squid/submissions/application/finalization.py`: “bad design.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791476301)
- [`squid/submissions/application/finalization.py`: “shouldn't these be their own handler, this is way too long of a function and the validation looks like they belong to some more sepcific classes”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791481026)
- [`squid/submissions/infrastructure/finalization_repository.py`: “there must be a better way. pydantic?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791496069)
- [`squid/api/v1/submission_media.py`: “defining everything here in one file is lazy, and wrong.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796630410)
- [`squid/api/v1/submission_media.py`: “we are not implementing a service in the api layer”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796635009)
- [`tests/unit/submissions/test_media_api_contract.py`: “this whole commit is too lazy and should be refactored heavvily”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796638770)
- [`squid/worker/app.py`: “ugly to have so many individual checks for self._services.media_runner”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796721953)
- [`squid/minecraft_auth/errors.py`: “??? This is completely different from how other errors are implemented”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791425659)
- [`squid/api/v1/minecraft_auth.py`: “cant fastapi validate UUID in headers directly”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791507956)
- [`squid/api/v1/minecraft_auth.py`: “why are we doing this”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791510883)
- [`squid/api/rate_limit.py`: “dont like this”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791966825)

## Naming, terminology, and documentation

- [`alembic/versions/2026_08_10_1900-e1f2a3b4c5d6_durable_schematic_render_projection.py`: “ban projection”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796148680)
- [`squid/schematics/application/ports.py`: “ban project”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796489853)
- [`squid/schematics/application/ports.py`: “confusing docstirng”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796491045)
- [`squid/idempotency/infrastructure/models.py`: “we are not using the word principal”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790727364)
- [`squid/records/infrastructure/models.py`: “the concept of \"Competition\" vs \"Definition\" vs just a normal \"Record\" is confusing”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790793074)
- [`squid/notifications/infrastructure/models.py`: “too specific of a docstring vs the class name”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790855154)
- [`squid/submissions/application/forms.py`: “many confusing docstrings”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790828133)
- [`squid/submissions/application/forms.py`: “ban provenance”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790831496)
- [`squid/submissions/application/forms.py`: “this is mainly for our internal inference pipeline, not for users”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790832051)
- [`squid/submissions/application/forms.py`: “a *Sponsor Server* is an internal concept. Change the wording.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790832509)
- [`squid/submissions/application/forms.py`: “not sure about calling these \"opening width\"”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790833459)
- [`squid/submissions/application/drafts.py`: “DraftIncompleteError doesn't read like the correct error here”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790886467)
- [`squid/submissions/errors.py`: “dont mention synchronized”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790889517)
- [`squid/media/errors.py`: “users won't understand \"normalized\" i think, and won't know what exactly are we trying to do and how we are failing.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790905436)
- [`squid/accounts/domain/models.py`: “why not identities(...)”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791071265)
- [`squid/accounts/domain/models.py`: “docstring seem inaccurate when ClaimStatus exists”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791104476)
- [`squid/accounts/domain/models.py`: “returned by persistence is not necessary”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791106450)
- [`squid/media/application/jobs.py`: “actually im not a fan of this being named Poster”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791222701)
- [`squid/submissions/application/finalization.py`: “again wrong error class pattern”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791470286)
- [`squid/submissions/application/finalization.py`: “not a good class name”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791470939)
- [`squid/submissions/application/finalization.py`: “not a good class name”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791471039)
- [`squid/submissions/application/finalization.py`: “ban \" without assuming a transport\"”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791471769)
- [`squid/submissions/errors.py`: “using the word media here is confusing. We should change it everywhere”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3803768419)
- [`squid/minecraft_auth/application/services.py`: “isnt this called clock elsewhere”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791372764)
- [`squid/minecraft_auth/infrastructure/repository.py`: “needs a comment on why an advisory lock is needed here.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791404140)

## Data modeling, typing, and persistence

- [`squid/idempotency/infrastructure/models.py`: “enum”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790746086)
- [`squid/idempotency/infrastructure/models.py`: “enum”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790746250)
- [`squid/api/rate_limit.py`: “subclass the protocol”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790767350)
- [`squid/records/infrastructure/models.py`: “enum”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790791549)
- [`squid/records/infrastructure/models.py`: “enum”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790792005)
- [`squid/accounts/infrastructure/models.py`: “merge this into id when we do the UUIDv7 revamp”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790796051)
- [`squid/notifications/infrastructure/models.py`: “enum”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790851725)
- [`squid/submissions/domain/forms.py`: “enum?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790880885)
- [`squid/submissions/infrastructure/models.py`: “enum”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790897637)
- [`squid/media/infrastructure/models.py`: “enum?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791226249)
- [`squid/accounts/infrastructure/repository.py`: “can we at least make this triple quoted string, and ideally using sqlalchemy”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791446925)
- [`squid/submissions/infrastructure/finalization_models.py`: “tripl;e quoted string please”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791484535)
- [`squid/submissions/infrastructure/finalization_models.py`: “enum?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791484840)
- [`squid/submissions/infrastructure/finalization_models.py`: “should have a big cleanup into something like UUIDv7AuditBase”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791485512)
- [`squid/submissions/infrastructure/finalization_models.py`: “1. what is target_key / 2. we are not storing a jsonb as provenance bro. Ban provenance, and wtf is this design.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791486676)
- [`squid/submissions/infrastructure/finalization_repository.py`: “no need to use .value”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791487536)
- [`squid/persistence/advisory_locks.py`: “namespace enum?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3803751461)

## Duplication, cleanup, and maintainability

- [`Dockerfile`: “don't like this. We can do better.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796045201)
- [`scripts/export_openapi.py`: “really need a ROOT constant”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3789569542)
- [`squid/api/v1/schemas/submissions.py`: “another definition for idempotency key”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790909256)
- [`squid/schematics/application/queries.py`: “ok we need a single round of big cleanup of historical baggage”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791237158)
- [`tests/integration/schematics/test_repository.py`: “use the constant”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791246495)
- [`squid/builds/application/commands.py`: “Delete this”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791448120)
- [`squid/builds/domain/models.py`: “delete this”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791448855)
- [`squid/submissions/application/finalization.py`: “these sort of utils exist in way too many locations”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791481744)
- [`squid/submissions/domain/finalization.py`: “doesnt these already exist, why are we duplicating again (a couple other classes here are duplicated too)”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791483042)
- [`squid/submissions/infrastructure/finalization_repository.py`: “again so many little utils scattered everywhere”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791496720)
- [`squid/media/infrastructure/repository.py`: “I feel like this should be a helper”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3803745065)
- [`squid/submissions/infrastructure/repository.py`: “is this a TODO?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3803760966)
- [`squid/minecraft_auth/infrastructure/accounts.py`: “are we sure this isnt duplicated with another repository”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791382593)
- [`squid/api/rate_limit.py`: “dont like this being separated strings”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791967918)

## Correctness, performance, and reliability

- [`squid/schematics/infrastructure/repository.py`: “only primary?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3789550075)
- [`squid/schematics/infrastructure/repository.py`: “doing this manually seem really error prone”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796514203)
- [`squid/api/v1/notifications.py`: “we are NOT filtering in memory”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790811050)
- [`squid/api/v1/notifications.py`: “this staff decision is wack”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790811493)
- [`squid/notifications/infrastructure/repository.py`: “can we mark unread too”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790858055)
- [`squid/notifications/infrastructure/repository.py`: “N+1?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790860328)
- [`squid/submissions/domain/drafts.py`: “don't like the fact that we are having multiple places to validate idempotency key format”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790838994)
- [`squid/media/domain/models.py`: “why only return one violation?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790902570)
- [`squid/api/v1/schemas/schematics.py`: “hmm, idk how to feel about hard coding an url that could be changed elsewhere in code”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791235798)

## Tests and CI

- [`tests/unit/api/test_rate_limit.py`: “subclass protocol”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790781476)
- [`.github/workflows/catalogue-screenshots-commit.yml`: “this is such a long script this should be extracted out maybe, if it can be done securely”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790822387)
- [`tests/unit/voting/application/test_vote_service.py`: “nah, we are not storing a 9-tuple without explaining what each row is. Can we just not have this.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791201333)
- [`tests/integration/builds/test_submission_targets.py`: “Don't use so many sql for this. maintenance hell.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791461730)
- [`tests/unit/builds/application/test_services.py`: “not worth testing. We should completely clean up these tests for removal of historical behavior”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791463041)
- [`tests/integration/builds/test_submission_targets.py`: “this is too many SQL to validate... either helpers or sqlalchemy”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796604869)
- [`tests/deployment/test_media_worker_image.py`: “don't test by reading strings...”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3799634252)
- [`tests/unit/submissions/test_api_contract.py`: “subclass”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3803659673)

## User experience, API contracts, and i18n

- [`squid/bot/notifications.py`: “don't return an ID...”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790813978)
- [`squid/bot/notifications.py`: “What an useless user-unfriendly description. This command shouldn't take in an ID.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790814832)
- [`squid/bot/notifications.py`: “in general, don't take in IDs in user facing commands.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790815751)
- [`squid/bot/notifications.py`: “Add a UI and most commands can be gone. (or be hidden, i think prefix commands are useful for testing)”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790817804)
- [`squid/bot/notifications.py`: “translation?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790819450)
- [`squid/api/v1/submission_media.py`: “translation”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796622125)
