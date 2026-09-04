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

function sortedEntries(entries) {
  if (!Array.isArray(entries)) {
    throw new TypeError('Screenshot artifact entries must be an array.');
  }
  return [...entries].sort((left, right) => left.name.localeCompare(right.name));
}

function validateScreenshotMetadata(entries) {
  const files = sortedEntries(entries);
  const names = files.map((entry) => entry.name);
  if (JSON.stringify(names) !== JSON.stringify(EXPECTED_SCREENSHOTS)) {
    throw new Error(`Screenshot artifact path allowlist mismatch: ${names.join(', ')}`);
  }

  let totalBytes = 0;
  const validatedFiles = files.map((entry) => {
    if (!entry.isFile || entry.isSymbolicLink) {
      throw new Error(`${entry.name} is not a regular file.`);
    }
    if (!Number.isSafeInteger(entry.size) || entry.size < 0) {
      throw new Error(`${entry.name} does not have a safe file size.`);
    }
    totalBytes += entry.size;
    if (totalBytes > MAX_TOTAL_BYTES) {
      throw new Error(`Screenshot artifact is ${(totalBytes / 1024 / 1024).toFixed(1)} MiB; maximum is 8 MiB.`);
    }
    return Object.freeze({
      name: entry.name,
      isFile: true,
      isSymbolicLink: false,
      size: entry.size,
    });
  });

  return Object.freeze({files: Object.freeze(validatedFiles), totalBytes});
}

function crc32(contents) {
  let crc = 0xffffffff;
  for (const byte of contents) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function validatePng(name, contents) {
  if (!Buffer.isBuffer(contents) || !contents.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE)) {
    throw new Error(`${name} does not have a PNG signature.`);
  }

  let offset = PNG_SIGNATURE.length;
  let chunkIndex = 0;
  let sawImageData = false;
  while (offset < contents.byteLength) {
    if (offset + 12 > contents.byteLength) {
      throw new Error(`${name} has a truncated PNG chunk.`);
    }
    const length = contents.readUInt32BE(offset);
    const type = contents.toString('ascii', offset + 4, offset + 8);
    const dataStart = offset + 8;
    const dataEnd = dataStart + length;
    const chunkEnd = dataEnd + 4;
    if (!/^[A-Za-z]{4}$/.test(type) || chunkEnd > contents.byteLength) {
      throw new Error(`${name} has invalid PNG chunk framing.`);
    }
    if (contents.readUInt32BE(dataEnd) !== crc32(contents.subarray(offset + 4, dataEnd))) {
      throw new Error(`${name} has an invalid PNG chunk checksum.`);
    }
    if (chunkIndex === 0 && (type !== 'IHDR' || length !== 13)) {
      throw new Error(`${name} does not start with a PNG IHDR chunk.`);
    }
    if (type === 'IHDR' && chunkIndex !== 0) {
      throw new Error(`${name} has a duplicate PNG IHDR chunk.`);
    }
    if (type === 'IDAT') {
      sawImageData = true;
    }
    if (type === 'IEND') {
      if (length !== 0 || !sawImageData || chunkEnd !== contents.byteLength) {
        throw new Error(`${name} has an invalid PNG IEND chunk.`);
      }
      return;
    }
    offset = chunkEnd;
    chunkIndex += 1;
  }
  throw new Error(`${name} does not end with a PNG IEND chunk.`);
}

function planScreenshotArtifact(entries) {
  const metadata = validateScreenshotMetadata(entries);
  const entriesByName = new Map(entries.map((entry) => [entry.name, entry]));
  const blobs = metadata.files.map((file) => {
    const entry = entriesByName.get(file.name);
    if (!Buffer.isBuffer(entry.contents) || entry.contents.byteLength !== file.size) {
      throw new Error(`${file.name} contents do not match the validated file size.`);
    }
    validatePng(file.name, entry.contents);
    return Object.freeze({
      path: `catalogue-screenshots/${file.name}`,
      mode: '100644',
      type: 'blob',
      content: entry.contents.toString('base64'),
      encoding: 'base64',
    });
  });

  return Object.freeze({blobs: Object.freeze(blobs), totalBytes: metadata.totalBytes});
}

module.exports = {
  EXPECTED_SCREENSHOTS,
  MAX_TOTAL_BYTES,
  planScreenshotArtifact,
  validateScreenshotMetadata,
};
