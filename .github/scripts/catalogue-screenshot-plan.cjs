'use strict';

const EXPECTED_SCREENSHOTS = Object.freeze([
  'en-desktop-build-detail.png',
  'en-desktop-builds.png',
  'en-desktop-home.png',
  'en-desktop-record-detail.png',
  'zh-cn-mobile-build-detail.png',
  'zh-cn-mobile-builds.png',
  'zh-cn-mobile-home.png',
  'zh-cn-mobile-record-detail.png',
]);
const MAX_TOTAL_BYTES = 8 * 1024 * 1024;
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function planScreenshotArtifact(entries) {
  if (!Array.isArray(entries)) {
    throw new TypeError('Screenshot artifact entries must be an array.');
  }

  const files = [...entries].sort((left, right) => left.name.localeCompare(right.name));
  const names = files.map((entry) => entry.name);
  if (JSON.stringify(names) !== JSON.stringify(EXPECTED_SCREENSHOTS)) {
    throw new Error(`Screenshot artifact path allowlist mismatch: ${names.join(', ')}`);
  }

  let totalBytes = 0;
  const blobs = files.map((entry) => {
    if (!entry.isFile || entry.isSymbolicLink) {
      throw new Error(`${entry.name} is not a regular file.`);
    }
    if (!Buffer.isBuffer(entry.contents) || !entry.contents.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE)) {
      throw new Error(`${entry.name} does not have a PNG signature.`);
    }
    totalBytes += entry.contents.byteLength;
    return Object.freeze({
      path: `catalogue-screenshots/${entry.name}`,
      mode: '100644',
      type: 'blob',
      content: entry.contents.toString('base64'),
      encoding: 'base64',
    });
  });

  if (totalBytes > MAX_TOTAL_BYTES) {
    throw new Error(`Screenshot artifact is ${(totalBytes / 1024 / 1024).toFixed(1)} MiB; maximum is 8 MiB.`);
  }

  return Object.freeze({blobs: Object.freeze(blobs), totalBytes});
}

module.exports = {EXPECTED_SCREENSHOTS, MAX_TOTAL_BYTES, planScreenshotArtifact};
