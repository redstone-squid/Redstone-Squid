import { existsSync } from "node:fs";
import type { Page, TestInfo } from "@playwright/test";
import { expect } from "@tests/support/test";

/** Annotation type the gallery reporter lists as a visual change. */
export const VISUAL_CHANGE = "visual-change";

/**
 * Attach a full-page screenshot to the review gallery.
 *
 * With `REVIEW_COMPARE=1`, which only CI sets, it is also compared with the baseline that
 * the last master run published. A difference or a missing baseline is recorded as an
 * annotation, not a failure: visual changes are reviewed in the gallery, not approved by
 * committing images. Master's run passes `--update-snapshots=all` to write that baseline.
 */
export async function captureForReview(
  page: Page,
  testInfo: TestInfo,
  name: string,
  animations: "allow" | "disabled",
): Promise<void> {
  const path = testInfo.outputPath(`${name}.png`);
  await page.screenshot({ path, fullPage: true, animations });
  await testInfo.attach(name, { path, contentType: "image/png" });
  if (process.env.REVIEW_COMPARE !== "1") return;
  // Playwright fails the test on a missing snapshot even when the error is caught.
  const baseline = testInfo.snapshotPath(`${name}.png`, { kind: "screenshot" });
  if (testInfo.config.updateSnapshots !== "all" && !existsSync(baseline)) {
    testInfo.annotations.push({ type: VISUAL_CHANGE, description: name });
    return;
  }
  try {
    // A short timeout: a mismatch retries until it expires, and the page is already settled.
    await expect(page).toHaveScreenshot(`${name}.png`, {
      fullPage: true,
      animations,
      timeout: 2_000,
    });
  } catch {
    testInfo.annotations.push({ type: VISUAL_CHANGE, description: name });
  }
}
