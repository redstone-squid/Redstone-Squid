# Adding a build attribute

1. Add the field once to `Build` or its category subclass in `squid/builds/models.py`. Define its type, default,
   units, and the meaning of `None`; add a short attribute docstring when useful. Create a
   [migration](new-migration.md) for the column and any backfill. Inserts and updates use the same entity;
   there is no domain-to-ORM field map to extend.
2. Add the field to the input paths that need it: `BuildDraft` for inference, submission forms and normalization
   for guided submissions, or `BuildEditPatch` for edits. Preserve existing form revisions and stored payload
   formats; update their explicit schemas when introducing a new version.
3. Expose it in the relevant API schema, Discord controls, or rendering. Review explicit search projections,
   public summaries, embeddings, and record calculations if they use the field.
4. Extend the relevant behavior tests. `tests/integration/builds/test_build_round_trip.py` checks persistence
   across insertion and update; also cover omission versus clearing and retained payload compatibility where
   applicable. Run focused tests with `--no-cov`, `just typecheck`, and `git diff --check`.

Repository reads return loaded, detached entities. Make changes through an explicit repository write;
use `copy_for_edit()` when preparing an isolated build preview.
