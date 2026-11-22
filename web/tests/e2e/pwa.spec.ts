import { expect, test } from "../support/test";

test("serves an installable web app manifest", async ({ request }) => {
  const manifest = (await (await request.get("/manifest.webmanifest")).json()) as {
    name: string;
    icons: { purpose?: string }[];
  };
  expect(manifest.name).toBe("Redstone Squid");
  expect(manifest.icons.some((icon) => icon.purpose === "maskable")).toBe(true);
});

test.describe("with a service worker", () => {
  test.use({ serviceWorkers: "allow" });

  test("shows the offline page in the visitor's locale when a navigation fails", async ({
    context,
    page,
  }) => {
    await page.goto("/zh-cn/records");
    await page.evaluate(async () => {
      await navigator.serviceWorker.ready;
    });
    // The worker controls pages loaded after it activates.
    await page.reload();
    await context.setOffline(true);
    await page.goto("/zh-cn/builds");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("你已离线");
  });
});
