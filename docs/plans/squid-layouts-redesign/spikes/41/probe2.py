import gc
import sys
import timeit
import weakref

sys.path.insert(0, ".")
import collector, graph, pull

# ---- leak: a computed on one component reading another component's state ----
print("cross-component reader dropped; is the source still holding it?")
for sl, name in ((collector, "A collector"), (graph, "B graph"), (pull, "C pull")):

    class Source(sl.Component):
        a = sl.state(1)

    class Reader(sl.Component):
        def __init__(self, source):
            self.source = source

        @sl.computed
        def doubled(self):
            return self.source.a * 2

    source = Source()
    reader = Reader(source)
    _ = reader.doubled
    ref = weakref.ref(reader)
    del reader
    gc.collect()
    print(f"  {name:<12} reader alive after drop: {ref() is not None}")

# ---- repeated derived reads with no writes (the render hot path) ----
print("\nrepeated reads of a 3-deep computed chain, no writes between (min of 7)")
for sl, name in ((collector, "A collector"), (graph, "B graph"), (pull, "C pull")):

    class Chain(sl.Component):
        a = sl.state(1)

        @sl.computed
        def one(self):
            return self.a + 1

        @sl.computed
        def two(self):
            return self.one + 1

        @sl.computed
        def three(self):
            return self.two + 1

    c = Chain()
    _ = c.three
    N = 50_000
    seconds = min(timeit.repeat(lambda: c.three, number=N, repeat=7))
    print(f"  {name:<12} {seconds / N * 1e9:7.0f} ns/read")

# ---- one write then one read, repeated (the action-then-render path) ----
print("\nwrite then read, 3-deep chain (min of 7)")
for sl, name in ((collector, "A collector"), (graph, "B graph"), (pull, "C pull")):

    class Chain(sl.Component):
        a = sl.state(1)

        @sl.computed
        def one(self):
            return self.a + 1

        @sl.computed
        def two(self):
            return self.one + 1

        @sl.computed
        def three(self):
            return self.two + 1

    c = Chain()
    _ = c.three
    n = [0]

    def cycle():
        n[0] += 1
        c.a = n[0]
        return c.three

    N = 20_000
    sl.STATS["recomputes"] = 0
    seconds = min(timeit.repeat(cycle, number=N, repeat=7))
    print(f"  {name:<12} {seconds / N * 1e9:7.0f} ns/cycle   {sl.STATS['recomputes'] / (N * 7):.1f} recomputes/cycle")
