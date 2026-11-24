import { appendFileSync, copyFileSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { Reporter, TestCase, TestResult } from "@playwright/test/reporter";
import { VISUAL_CHANGE } from "@tests/support/review-screenshot";

const escapeHtml = (value: string): string =>
  value.replaceAll(/[&<>"']/g, (character) => `&#${character.charCodeAt(0)};`);

/**
 * Collect screenshots, recordings, and failures into a portable review artifact. Screenshots
 * that differ from master's baseline come first, with Playwright's expected, actual, and diff
 * images, and are listed in the GitHub job summary.
 */
export default class GalleryReporter implements Reporter {
  private readonly directory = path.resolve("review-gallery");
  private readonly entries: string[] = [];
  private readonly changes: string[] = [];
  private readonly changedNames: string[] = [];
  private sequence = 0;

  onBegin(): void {
    rmSync(this.directory, { recursive: true, force: true });
    mkdirSync(this.directory, { recursive: true });
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    const media: string[] = [];
    for (const attachment of result.attachments) {
      if (
        attachment.path === undefined ||
        !["image/png", "video/webm"].includes(attachment.contentType)
      )
        continue;
      const video = attachment.contentType === "video/webm";
      const filename = `${this.sequence++}.${video ? "webm" : "png"}`;
      copyFileSync(attachment.path, path.join(this.directory, filename));
      const label = escapeHtml(attachment.name);
      media.push(
        video
          ? `<figure><video controls preload="metadata" src="${filename}" aria-label="${label}"></video><figcaption>${label}</figcaption></figure>`
          : `<figure><a href="${filename}"><img loading="lazy" src="${filename}" alt="${label}"></a><figcaption>${label}</figcaption></figure>`,
      );
    }
    const title = escapeHtml(test.titlePath().filter(Boolean).join(" / "));
    const changed = result.annotations
      .filter((annotation) => annotation.type === VISUAL_CHANGE)
      .map((annotation) => `${test.parent.project()?.name ?? ""}/${annotation.description ?? ""}`);
    this.changedNames.push(...changed);
    (changed.length > 0 ? this.changes : this.entries).push(
      `<section><h2>${title}</h2><p>${escapeHtml(result.status)}, attempt ${result.retry + 1}${changed.length > 0 ? ", changed from master" : ""}</p>${media.length > 0 ? media.join("") : "<p>No media captured. See the Playwright report for test details.</p>"}</section>`,
    );
  }

  onEnd(): void {
    const summary = process.env.GITHUB_STEP_SUMMARY;
    if (summary !== undefined && process.env.REVIEW_COMPARE === "1") {
      const list = this.changedNames.map((name) => `- \`${name}\``).join("\n");
      appendFileSync(
        summary,
        this.changedNames.length > 0
          ? `### Screenshots changed from master\n\n${list}\n\nOpen review-gallery/index.html in the artifact; changes are listed first with their diffs.\n`
          : "### Screenshots match master\n",
      );
    }
    writeFileSync(
      path.join(this.directory, "index.html"),
      `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Redstone Squid UI review</title><style>body{font:16px/1.5 system-ui;background:#f5f5f4;color:#202623;margin:0;padding:24px}main{max-width:1100px;margin:auto}section{background:white;border:1px solid #d3d8d3;border-radius:12px;padding:24px;margin:24px 0}h2{font-size:18px}figure{margin:16px 0}img,video{max-width:100%;max-height:700px;object-fit:contain;object-position:left top}a:focus-visible,video:focus-visible{outline:3px solid #245bc0}figcaption{color:#59635d}</style></head><body><main><h1>Redstone Squid / UI review</h1><p>Commit: ${escapeHtml(process.env.GITHUB_SHA ?? "local working tree")}</p><p>Desktop and mobile, English and Chinese, light and dark. Open an image to inspect it at full size.</p>${this.changes.length > 0 ? `<h2>Changed from master</h2>${this.changes.join("\n")}<h2>Unchanged</h2>` : ""}${this.entries.join("\n")}</main></body></html>`,
    );
  }
}
