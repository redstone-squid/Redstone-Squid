import { globSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { compileRedirects, findRedirect, REDIRECTS, redirectProblems } from "@/server/redirects";

/** Route patterns from src/pages, with `[name]` segments written as `:name`. */
const routes = globSync("src/pages/**/*.{astro,ts}")
  .map((file) =>
    file
      .replace(/^src\/pages/, "")
      .replace(/\.(astro|ts)$/, "")
      .replace(/\/index$/, "")
      .replace(/\[([a-z][a-zA-Z0-9]*)\]/g, ":$1"),
  )
  .map((route) => (route === "" ? "/" : route));

describe("redirect table", () => {
  it("only points at real routes, never chains, and never shadows a route", () => {
    expect(redirectProblems(REDIRECTS, routes)).toEqual([]);
  });

  it("reports each kind of mistake", () => {
    expect(
      redirectProblems(
        {
          "/old-records": "/gone",
          "/a": "/b",
          "/b": "/records",
          "/records": "/builds",
          "/build/:id": "/builds/:buildId",
        },
        ["/records", "/builds", "/builds/:id"],
      ),
    ).toEqual([
      "/old-records: target /gone is not a route",
      "/a: target /b is not a route",
      "/a: target /b redirects again",
      "/b: target /records redirects again",
      "/records: shadows an existing route",
      "/build/:id: target uses :buildId, which the source lacks",
    ]);
  });
});

describe("findRedirect", () => {
  const table = compileRedirects({
    "/build/:id": "/builds/:id",
    "/catalogue": "/builds",
  });

  it("carries parameters into the new path", () => {
    expect(findRedirect("/build/7", table)).toBe("/builds/7");
    expect(findRedirect("/catalogue/", table)).toBe("/builds");
  });

  it("matches whole segments only", () => {
    expect(findRedirect("/build/7/extra", table)).toBeUndefined();
    expect(findRedirect("/catalogues", table)).toBeUndefined();
  });
});
