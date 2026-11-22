import type { Page, TestInfo } from "@playwright/test";

export async function attachReviewScreenshot(
  page: Page,
  testInfo: TestInfo,
  name: string,
  animations: "allow" | "disabled",
): Promise<void> {
  const path = testInfo.outputPath(`${name}.png`);
  await page.screenshot({ path, fullPage: true, animations });
  await testInfo.attach(name, { path, contentType: "image/png" });
}
