'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
  EXPECTED_SCREENSHOTS,
  MAX_TOTAL_BYTES,
  planScreenshotArtifact,
} = require('./catalogue-screenshot-plan.cjs');

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function validEntries() {
  return EXPECTED_SCREENSHOTS.map((name) => ({
    name,
    isFile: true,
    isSymbolicLink: false,
    contents: Buffer.concat([PNG_SIGNATURE, Buffer.from(name)]),
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
  assert.throws(
    () => planScreenshotArtifact([{...validEntries()[0], contents: Buffer.from('not png')}, ...validEntries().slice(1)]),
    /does not have a PNG signature/,
  );
});

test('rejects artifacts above the aggregate size limit', () => {
  const entries = validEntries();
  entries[0] = {
    ...entries[0],
    contents: Buffer.concat([PNG_SIGNATURE, Buffer.alloc(MAX_TOTAL_BYTES)]),
  };

  assert.throws(() => planScreenshotArtifact(entries), /maximum is 8 MiB/);
});
