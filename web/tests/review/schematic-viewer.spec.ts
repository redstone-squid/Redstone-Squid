import * as m from "@/locales/messages.gen";
import { expect, test } from "@tests/support/test";

// Catches a renderer that the production CSP blocks (its blob workers, data-URL WebAssembly
// and generated textures), which the shared fixture reports as console errors, or that never
// draws the schematic.
test("a schematic opens in 3D under the production policy", async ({ page }) => {
  test.setTimeout(90_000);
  // The workbench has no API; the renderer package ships a default pack for its own demos.
  await page.route("**/v1/schematic-resource-pack", (route) =>
    route.fulfill({
      path: "node_modules/schematic-renderer/dist/pack.zip",
      contentType: "application/zip",
    }),
  );
  await page.goto("/design-system/patterns#schematic-viewer");
  const story = page.locator("#schematic-viewer");
  await story.getByRole("button", { name: m.schematicViewerOpen("en") }).click();
  // The renderer and Nucleation's WebAssembly are several megabytes; meshing runs in workers.
  await expect(story.getByText(m.schematicViewerHelp("en"))).toBeVisible({ timeout: 60_000 });
  const view = story.getByRole("group", {
    name: m.schematicViewerLabel({ title: "Copper Bolt 5×5" }, "en"),
  });
  await expect(view.locator("canvas")).toBeVisible();
  await view.screenshot({ path: test.info().outputPath("schematic-viewer.png") });
});
