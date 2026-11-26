import { expect, it } from "vitest";
import { workbench } from "@/workbench/appearance";

it("pins the resolved theme inside the workbench and leaves inherited contrast implicit", () => {
  const request = {
    url: new URL("https://example.test/design-system-trial?scenario=retry&contrast=invalid"),
    locals: { theme: "dark", contrast: "more" } as const,
  };
  const { nav, effectiveContrast } = workbench.at(request);
  expect(effectiveContrast).toBe("more");
  expect(nav.link(workbench.group("patterns"))).toBe("/design-system/patterns?theme=dark");
  expect(nav.link(workbench.home)).toBe("/");
  expect(nav.link(workbench.trial, { query: { scenario: "retry" }, fragment: "upload" })).toBe(
    "/design-system-trial?theme=dark&scenario=retry#upload",
  );
  expect(nav.update({ contrast: "standard" })).toBe(
    "/design-system-trial?scenario=retry&theme=dark&contrast=standard",
  );
});
