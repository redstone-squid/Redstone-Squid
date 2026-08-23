"""Two things the scenario table only hints at: the glitch, and read cost."""

import sys
import timeit

sys.path.insert(0, ".")

import collector
import graph

# ---- glitch: does anything observe an inconsistent intermediate value? ----
for sl, name in ((collector, "A collector"), (graph, "B graph")):
    seen = []

    class Panel(sl.Component):
        a = sl.state(1)

        @sl.computed
        def left(self):
            return self.a * 2

        @sl.computed
        def right(self):
            return self.a * 3

        @sl.computed
        def total(self):
            value = self.left + self.right
            seen.append(value)
            return value

    p = Panel()
    _ = p.total
    seen.clear()
    p.a = 2
    _ = p.total
    print(f"{name:<12} total computed as {seen}  (consistent answer is [10])")

# ---- read cost, against the shipping implementation ----
import squid_layouts as sl_real


class Real(sl_real.Component):
    a: int = sl_real.state(1)

    def render(self):
        raise NotImplementedError


class ColPanel(collector.Component):
    a = collector.state(1)


class GraphPanel(graph.Component):
    a = graph.state(1)


N = 100_000
real, col, gra = Real(), ColPanel(), GraphPanel()


def best(read):
    return min(timeit.repeat(read, number=N, repeat=7))


results = {
    "shipping _State": best(lambda: real.a),
    "A collector": best(lambda: col.a),
    "B graph": best(lambda: gra.a),
}
base = results["shipping _State"]
print()
for label, seconds in results.items():
    print(f"{label:<16} {seconds / N * 1e9:7.0f} ns/read   {seconds / base:5.2f}x")
