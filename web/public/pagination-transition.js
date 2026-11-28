/* global URL, addEventListener, window */
function setPaginationDirection(transition, activation) {
  if (!transition || !activation?.from?.url || !activation.entry?.url) return;
  const from = new URL(activation.from.url);
  const to = new URL(activation.entry.url);
  const delta =
    Number(to.searchParams.get("page") ?? 1) - Number(from.searchParams.get("page") ?? 1);
  for (const url of [from, to]) {
    url.searchParams.delete("page");
    url.searchParams.sort();
  }
  if (from.pathname === to.pathname && from.search === to.search && delta) {
    transition.types?.add(delta > 0 ? "page-forward" : "page-back");
  }
}
addEventListener("pageswap", (event) =>
  setPaginationDirection(event.viewTransition, event.activation),
);
addEventListener("pagereveal", (event) =>
  setPaginationDirection(event.viewTransition, window.navigation?.activation),
);
