import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { gzipSync } from "node:zlib";

const budgetBytes = 100 * 1024;
const assetDirectory = path.resolve("dist/client/_astro");
const assets = (await readdir(assetDirectory)).filter((file) => file.endsWith(".js")).sort();

if (assets.length === 0) {
  throw new Error(
    `No client JavaScript assets found in ${assetDirectory}; run the production build first.`,
  );
}

const sizes = await Promise.all(
  assets.map(async (asset) => ({
    asset,
    bytes: gzipSync(await readFile(path.join(assetDirectory, asset)), { level: 9 }).byteLength,
  })),
);
const totalBytes = sizes.reduce((total, item) => total + item.bytes, 0);

for (const { asset, bytes } of sizes) {
  console.info(`${asset}: ${(bytes / 1024).toFixed(1)} KiB gzip`);
}
console.info(
  `Total client JavaScript: ${(totalBytes / 1024).toFixed(1)} KiB gzip / 100.0 KiB budget`,
);

if (totalBytes > budgetBytes) {
  throw new Error(
    `Client JavaScript exceeds the gzip budget by ${((totalBytes - budgetBytes) / 1024).toFixed(1)} KiB.`,
  );
}
