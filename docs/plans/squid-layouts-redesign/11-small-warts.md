# 11 — Small warts sweep

One cleanup pass for the audit findings too small to justify their own plan. Each item
is an independent commit; none blocks or is blocked by plans 01–10 except where noted.

1. **`Composition.interventions`** (`discord/compose.py:39-42`): vestigial always-empty
   property retained from the pre-solver clamping era. Delete it and its callers (grep
   first; the migration doc says drawing no longer clamps).
2. **`state()` returns `Any`** (`runtime/reactivity.py:364-371`): typed overloads —
   `state(default: T) -> T`, `state(*, factory: Callable[[], T]) -> T`. Folded into plan
   08 item 4 if that lands first; do here otherwise.
3. **`Choices(minimum=1)` default** (`semantic.py:283-291`): an optional select
   (`minimum=0`) is legal on Discord but the default makes deselect-all unrepresentable
   without thinking about it. Keep the default (changing it silently changes existing
   pickers) but document it on the dataclass and check that `minimum=0` actually lowers
   correctly (adaptation passes `min_values` straight through — add a test).
4. **`ActionEvent.context` typing** (`actions.py:56`): document the reserved
   `"frontend"` key on the dataclass; covered by plan 02 item 3 — verify, don't
   duplicate.
5. **`Mount.build_view` naming**: after plan 01, `build_view` is stage-without-commit;
   rename or docstring accordingly so hosts don't assume it is side-effect-free (it
   still renders the component tree).
6. **Dead `DisclosureState` check**: covered by plan 10 item 3 — verify, don't
   duplicate.
7. **`_custom_id` digest note** (`discord/mount.py:67-82`): fine as-is; add a test
   pinning the >100-char collision behavior (two long keys sharing a prefix must not
   collide after digesting) since nothing covers it today.

## Verification

Per-item focused tests (`test_mount.py`, `test_semantic_structures.py`,
`test_public_api.py` for removed exports), `just typecheck`, `git diff --check`.
