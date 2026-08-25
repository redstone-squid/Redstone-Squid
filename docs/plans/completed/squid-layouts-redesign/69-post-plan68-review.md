# Plan 69: post-Plan 68 review fixes

Status: complete, landed 2026-08-25

Seven defects found reviewing Plan 68 after it landed, spanning three packages. Six of the
seven were the same shape: something was lost and nothing said so. Each fix either preserves
the information the runtime needed or makes the loss loud; none of them buys correctness by
retaining more.

## Outcomes

1. **Replicated-set remove undo.** Fixed by preserving remover identity rather than by
   rejecting the inverse. `FakeOperation` gained an `undoes` field and a fourth kind,
   `restore`, that names the removal it reverses; presence is now decided by
   `_standing_removals`, so an add-tag is live only when *every* removal naming it has been
   reversed. Undoing one replica's removal no longer cancels a concurrent one. This also fixed
   an unlisted sibling: discarding an absent value emits a removal with no tags, and its old
   inverse added unconditionally, so undo could insert a value that never existed. The envelope
   stayed at schema 1 with `undoes` optional-absent, so tokens encoded before the change still
   decode.
2. **Replica ID reuse.** Fixed by restoring the clock, not by demanding unique IDs. Apply
   advances the sequence to the high-water mark of every operation this replica minted, and it
   is the single funnel for local commits and remote imports alike, so importing peer history
   carries the counter past the previous incarnation. Apply also stopped resolving a genuine
   collision with `setdefault`, which kept the incumbent -- meaning the operation silently
   dropped was the user's new write, not the stale copy. `ReplicatedScope` now documents the
   contract it depends on.
3. **Outbound update overflow.** Fixed by marking, not by unbounding. Overflow counts the drop
   and sets a sticky flag; `drain_updates` raises `ReplicatedResyncRequiredError` rather than
   returning a stream known to have a hole in it, and `acknowledge_resync` clears it after the
   host exports from the peer's version. An unbounded outbox was considered and rejected: it is
   in-memory, so a process death loses it either way, which buys no durability in exchange for
   an OOM path. The retention-bound test drove the deque directly and so could not have caught
   this; it now overflows through the real publish path.
4. **History admission failure.** Undo and redo now restore the state the entry was *selected*
   in rather than a fixed `READY`/`UNDONE`, because an entry sitting at `CONFLICTED` or
   `FAILED` is still selectable. The compensation path had the same bug in a worse form and was
   not in the original finding: it has no integrity arm at all, so a refused admission was
   reported as a `FAILED` compensation. It re-raises now, which also stops
   `FrameworkIntegrityError` being classified as a safe failure there.
5. **`strong_read()` documentation.** The action-ledger example was corrected to opt in, and
   all three sources of a precondition are now named rather than left to be inferred. Both
   package READMEs had the milder version of the same problem and now say how a read becomes
   strong. The claim is pinned by tests at both polarities: the corrected example is the
   existing superseded-read test, and its negative -- the same code without `strong_read()`
   committing cleanly -- is new.
6. **Session quota replacement.** Both quota checks now count against the membership the open
   will leave behind, excluding the resolved victims. `join` excludes nothing, because a join
   retires no session. This was reachable on the *default* policy and had a live consumer: the
   showcase lobby opens with `quota=1`, so a second `/layout lobby` by the same author in the
   same guild failed outright instead of replacing.
7. **ChallengeRunner bounds.** A semaphore permit is taken before the task is started rather
   than inside it, so the loop stops dequeuing while saturated and the queue depth becomes real
   backpressure. A press that never returns now holds its slot; that is what a bound costs, and
   the class docstring says so.

## Notes for the next reader

- `histories.py` uses PEP 758 unparenthesized `except A, B:`. It is valid on this project's
  Python 3.14 and parses cleanly under `uv run python`; the system `python3` is older and
  reports a SyntaxError. Do not "fix" it.
- `packages/squid-replicated/tests` was outside `testpaths`, coverage, and the architecture
  scans, so three of these fixes would have shipped untested in CI. All four were added.
  Adding the package to the architecture scans surfaced no new violations.
- Every fix has a regression test that was confirmed to fail against the previous behaviour,
  except where the test pins pre-existing contract behaviour and says so.

## Verification performed

- 57 focused `squid-replicated` tests, 31 `test_history.py`, 57 `test_sessions.py`, 30
  `test_challenges.py`, 34 `tests/unit/bot/test_layout_showcase.py`, plus `test_operations.py`,
  `test_transactions.py`, `test_mount.py`, `test_host.py`, `test_guards.py`,
  `test_durable_runtime.py` and `test_screens.py`. All pass.
- `tests/architecture` fails the same two ways as before this plan
  (`test_only_the_discord_transport_uses_the_layouts_package` and
  `test_one_public_name_means_one_class`); neither involves `squid_replicated`.
- Ruff check and format over every changed file, `alembic heads`, and `git diff --check`.
- The repository-wide suite was not run on the development box, per `CLAUDE.md`.
