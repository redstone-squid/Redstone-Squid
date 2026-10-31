# 11 — Small warts sweep

**Shipped.** Landed as `compose: delete vestigial Composition.interventions`,
`semantic: document Choices(minimum=1) and test minimum=0`, and `mount: pin the
_custom_id digest collision behavior`. Items 2, 4, 5, 6 turned out to already be
resolved by other plans by the time this one ran; verified in place rather than
duplicated.

One cleanup pass for the audit findings too small to justify their own plan. Each item
is an independent commit; none blocks or is blocked by plans 01–10 except where noted.

1. **`Composition.interventions`** (`discord/compose.py:39-42`): vestigial always-empty
   property retained from the pre-solver clamping era. Deleted, along with its two
   test-only callers in `test_compositor.py`.
2. **`state()` returns `Any`** (`runtime/reactivity.py:364-371`): typed overloads —
   `state(default: T) -> T`, `state(*, factory: Callable[[], T]) -> T`. Landed with plan
   08 item 4 (`sl.state()` gained typed overloads) before this plan ran; verified the
   overloads exist, nothing to do here.
3. **`Choices(minimum=1)` default** (`semantic.py`): an optional select (`minimum=0`) is
   legal on Discord but the default makes deselect-all unrepresentable without thinking
   about it. Kept the default (changing it silently changes existing pickers), documented
   it on the dataclass, and added a test pinning that `minimum=0` actually lowers
   `min_values` to 0 through adaptation.
4. **`ActionEvent.context` typing** (`actions.py`): document the reserved `"frontend"`
   key on the dataclass. Already done as plan 02 item 3 (the class docstring documents
   the key and steers Discord-only handlers to `sl.discord.native`/`responder`);
   verified, not duplicated.
5. **`Mount.build_view` naming**: after plan 01, `build_view` is stage-without-commit.
   Already documented: its docstring states staging is not committing and that
   "Rendering the component tree is the one side effect staging cannot avoid," and
   `discord/testing.py`'s `commit_render` docstring repeats the point for test authors.
   No rename needed; verified, not duplicated.
6. **Dead `DisclosureState` check**: covered by plan 10 item 3, which found the premise
   stale — `DisclosureState` was never unwired (`_details` both read and wrote it).
   Verified still true in the current tree; not duplicated.
7. **`_custom_id` digest note** (`discord/mount.py:68-83`): fine as-is; added
   `test_custom_id_digests_do_not_collide_across_a_shared_prefix` in `test_mount.py`
   pinning the >100-char collision behavior (two long keys sharing a prefix must not
   collide after digesting), which nothing covered before.

## Verification

Per-item focused tests (`test_mount.py`, `test_semantic_structures.py`,
`test_public_api.py` for removed exports), `just typecheck`, `git diff --check`.
