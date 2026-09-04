'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
  EXPECTED_SCREENSHOTS,
  MAX_TOTAL_BYTES,
  planScreenshotArtifact,
  validateScreenshotMetadata,
} = require('./catalogue-screenshot-plan.cjs');

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const VALID_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
);

function validEntries() {
  return EXPECTED_SCREENSHOTS.map((name) => ({
    name,
    isFile: true,
    isSymbolicLink: false,
    size: VALID_PNG.byteLength,
    contents: VALID_PNG,
  }));
}

test('plans the exact allowlisted PNG tree without side effects', () => {
  const plan = planScreenshotArtifact(validEntries().reverse());

  assert.deepEqual(
    plan.blobs.map(({path, mode, type, encoding}) => ({path, mode, type, encoding})),
    EXPECTED_SCREENSHOTS.map((name) => ({
      path: `catalogue-screenshots/${name}`,
      mode: '100644',
      type: 'blob',
      encoding: 'base64',
    })),
  );
  assert.equal(plan.totalBytes, validEntries().reduce((total, entry) => total + entry.contents.byteLength, 0));
});

test('rejects extra files and path traversal', () => {
  assert.throws(
    () => planScreenshotArtifact([...validEntries(), {...validEntries()[0], name: 'extra.png'}]),
    /path allowlist mismatch/,
  );
  assert.throws(
    () => planScreenshotArtifact([{...validEntries()[0], name: '../outside.png'}, ...validEntries().slice(1)]),
    /path allowlist mismatch/,
  );
});

test('rejects symlinks and non-regular entries', () => {
  assert.throws(
    () => planScreenshotArtifact([{...validEntries()[0], isSymbolicLink: true}, ...validEntries().slice(1)]),
    /not a regular file/,
  );
  assert.throws(
    () => planScreenshotArtifact([{...validEntries()[0], isFile: false}, ...validEntries().slice(1)]),
    /not a regular file/,
  );
});

test('rejects data without the PNG signature', () => {
  const contents = Buffer.from('not png');
  assert.throws(
    () =>
      planScreenshotArtifact([
        {...validEntries()[0], size: contents.byteLength, contents},
        ...validEntries().slice(1),
      ]),
    /does not have a PNG signature/,
  );
});

test('rejects signature-prefixed data without a PNG chunk structure', () => {
  const contents = Buffer.concat([PNG_SIGNATURE, Buffer.from('not png')]);
  assert.throws(
    () =>
      planScreenshotArtifact([
        {...validEntries()[0], size: contents.byteLength, contents},
        ...validEntries().slice(1),
      ]),
    /truncated PNG chunk/,
  );
});

test('rejects a PNG with a corrupt chunk checksum', () => {
  const contents = Buffer.from(VALID_PNG);
  contents[20] ^= 1;
  assert.throws(
    () => planScreenshotArtifact([{...validEntries()[0], contents}, ...validEntries().slice(1)]),
    /invalid PNG chunk checksum/,
  );
});

test('rejects contents that changed after metadata validation', () => {
  assert.throws(
    () =>
      planScreenshotArtifact([
        {...validEntries()[0], size: VALID_PNG.byteLength + 1},
        ...validEntries().slice(1),
      ]),
    /contents do not match the validated file size/,
  );
});

test('rejects artifacts above the aggregate size limit without reading contents', () => {
  const entries = validEntries();
  entries[0] = {
    ...entries[0],
    size: MAX_TOTAL_BYTES,
  };
  Object.defineProperty(entries[0], 'contents', {
    get() {
      throw new Error('metadata validation read file contents');
    },
  });

  assert.throws(() => validateScreenshotMetadata(entries), /maximum is 8 MiB/);
});
