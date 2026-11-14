import { copyFileSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { Reporter, TestCase, TestResult } from "@playwright/test/reporter";

const escapeHtml = (value: string): string =>
  value.replace(/[&<>"']/g, (character) => `&#${character.charCodeAt(0)};`);

/** Collect successful screenshots and recordings as well as failures into a portable review artifact. */
export default class GalleryReporter implements Reporter {
  private readonly directory = path.resolve("review-gallery");
  private readonly entries: string[] = [];
  private sequence = 0;

  onBegin(): void {
    rmSync(this.directory, { recursive: true, force: true });
    mkdirSync(this.directory, { recursive: true });
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    const media: string[] = [];
    for (const attachment of result.attachments) {
      if (!attachment.path || !["image/png", "video/webm"].includes(attachment.contentType))
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
    this.entries.push(
      `<section><h2>${title}</h2><p>${escapeHtml(result.status)} · attempt ${result.retry + 1}</p>${media.join("") || "<p>No media captured. See the Playwright report for test details.</p>"}</section>`,
    );
  }

  onEnd(): void {
    writeFileSync(
      path.join(this.directory, "index.html"),
      `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Redstone Squid · UI review</title><style>body{font:16px/1.5 system-ui;background:#f5f5f4;color:#202623;margin:0;padding:24px}main{max-width:1100px;margin:auto}section{background:white;border:1px solid #d3d8d3;border-radius:12px;padding:24px;margin:24px 0}h2{font-size:18px}figure{margin:16px 0}img,video{max-width:100%;max-height:700px;object-fit:contain;object-position:left top}a:focus-visible,video:focus-visible{outline:3px solid #245bc0}figcaption{color:#59635d}</style></head><body><main><h1>Redstone Squid / UI review</h1><p>Commit: ${escapeHtml(process.env.GITHUB_SHA ?? "local working tree")}</p><p>Desktop and mobile · English and Chinese · light and dark. Open an image to inspect it at full size.</p>${this.entries.join("\n")}</main></body></html>`,
    );
  }
}
