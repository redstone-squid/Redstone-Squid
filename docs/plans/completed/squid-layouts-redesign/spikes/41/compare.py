"""Run identical scenarios against both prototypes.

The scenario bodies are written once and parameterised by the module, which is itself
a result: both candidates keep `self.count` and drop `depends=`, so the author-facing
code is byte-identical and only the propagation differs.
"""

import sys
from dataclasses import dataclass

from immutability import MutableStateError

sys.path.insert(0, ".")


def build(sl):
    class Panel(sl.Component):
        a = sl.state(1)
        flag = sl.state(True)
        x = sl.state("x")
        y = sl.state("y")
        rows = sl.state(())

        @sl.computed
        def either(self):
            return self.x if self.flag else self.y

        @sl.computed
        def left(self):
            return self.a * 2

        @sl.computed
        def right(self):
            return self.a * 3

        @sl.computed
        def total(self):
            return self.left + self.right

        @sl.computed
        def constant(self):
            return self.a * 0

        @sl.computed
        def above_constant(self):
            return self.constant + 1

        @sl.computed
        def never_read(self):
            return self.a * 100

    return Panel


def run(sl, name):
    Panel = build(sl)
    out = {}

    # 1. the deep-object hole: a value that cannot be snapshotted is refused
    p = Panel()
    try:
        p.rows = [1, 2]
        out["rejects list"] = False
    except MutableStateError:
        out["rejects list"] = True
    try:
        p.rows = (1, [2])          # the case an annotation check cannot see
        out["rejects nested"] = False
    except MutableStateError:
        out["rejects nested"] = True

    @dataclass(frozen=True)
    class Bad:
        tags: list

    @dataclass(frozen=True)
    class Good:
        tags: tuple

    try:
        p.rows = Bad([1])
        out["rejects frozen-dc-with-list"] = False
    except MutableStateError:
        out["rejects frozen-dc-with-list"] = True
    p.rows = Good((1,))
    out["accepts frozen-dc"] = True

    # 2. automatic tracking: no depends=, and the value is never stale
    p = Panel()
    p.flag = True
    first = p.either
    p.x = "X"
    out["auto-tracked"] = (first == "x" and p.either == "X")

    # 3. conditional dependency: while flag is False, x must not matter
    p = Panel()
    p.flag = False
    _ = p.either
    sl.STATS["recomputes"] = 0
    p.x = "ignored"
    out["conditional recomputes"] = sl.STATS["recomputes"]

    # 4. diamond: a -> left, a -> right, left+right -> total
    p = Panel()
    _ = p.total
    sl.STATS["recomputes"] = 0
    p.a = 2
    _ = p.total
    out["diamond recomputes"] = sl.STATS["recomputes"]
    out["diamond correct"] = p.total == 10

    # 5. equality cut-off: constant's inputs move, its value does not
    p = Panel()
    _ = p.above_constant
    sl.STATS["recomputes"] = 0
    p.a = 7
    _ = p.above_constant
    out["cut-off recomputes"] = sl.STATS["recomputes"]

    # 6. a computed nobody reads
    p = Panel()
    _ = p.left
    sl.STATS["recomputes"] = 0
    p.a = 3
    out["work before read"] = sl.STATS["recomputes"]

    # 7. an action handler's read must not create a dependency
    p = Panel()
    _ = p.left
    with sl.untracked():
        _ = p.a
    out["untracked read"] = True

    # 8. read cost on the hot path
    p = Panel()
    _ = p.left
    sl.STATS["reads"] = 0
    for _ in range(1000):
        _ = p.a
    out["reads per 1000 gets"] = sl.STATS["reads"]

    return out


import collector
import graph
import pull

left = run(collector, "collector")
right = run(graph, "graph")
third = run(pull, "pull")

width = max(len(k) for k in left)
print(f"{'scenario':<{width}}   {'A collector':>12}   {'B graph':>12}   {'C pull':>12}")
print("-" * (width + 48))
for key in left:
    print(f"{key:<{width}}   {str(left[key]):>12}   {str(right[key]):>12}   {str(third[key]):>12}")
