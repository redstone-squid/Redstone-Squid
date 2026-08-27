"""Pins that a dialect mismatch is a *static* error at any nesting depth. Nothing here runs.

This is the file the typing pass exists for. `typing_targets.py` already pinned that a bare
V2-only primitive cannot be planned for the classic target; what it could not pin, because
the mode stopped propagating one level down, is the case an author actually hits -- the
offending node three containers deep inside something that looks portable.

Every `pyrefly: ignore` below is an assertion that the line *is* an error. If one ever goes
unused, propagation has regressed and the guarantee is gone.
"""

from typing import assert_type

import squid_ui as sl
from squid_ui.planning import ClassicTarget, ComponentsV2Target, plan
from squid_ui.primitives import Card, Panel, Text
from squid_ui.semantic import Stack
from squid_ui_discord.target import classic, v2

# --- a container takes the meet of its children's modes ----------------------------------

portable = sl.stack(sl.heading("title"), sl.paragraph("body"))
assert_type(portable, Stack[sl.RenderTarget])

v2_only = sl.stack(sl.heading("title"), Panel((Text("inner"),)))
assert_type(v2_only, Stack[ComponentsV2Target])

classic_only = sl.stack(sl.heading("title"), Card(children=(Text("inner"),)))
assert_type(classic_only, Stack[ClassicTarget])

# --- and the meet survives nesting, wrappers, and plain strings ---------------------------

assert_type(sl.stack(sl.stack(sl.stack(Panel(())), "text"), None), Stack[ComponentsV2Target])
assert_type(sl.truncate(sl.stack(Panel(()))), sl.semantic.Truncated[ComponentsV2Target])
assert_type(sl.section(sl.heading("h"), sl.aside(Panel(()))), sl.semantic.Section[ComponentsV2Target])

# --- which makes the mismatch a type error where the author wrote it ----------------------

plan(portable, target=classic())
plan(portable, target=v2())
plan(v2_only, target=v2())
plan(classic_only, target=classic())

plan(v2_only, target=classic())  # pyrefly: ignore[no-matching-overload, bad-argument-type]
plan(classic_only, target=v2())  # pyrefly: ignore[no-matching-overload, bad-argument-type]

# Three containers deep, which is the case `typing_targets.py` could not reach.
buried = sl.stack(sl.section(sl.heading("h"), sl.aside(sl.stack(Panel(())))))
plan(buried, target=classic())  # pyrefly: ignore[no-matching-overload, bad-argument-type]
plan(buried, target=v2())
