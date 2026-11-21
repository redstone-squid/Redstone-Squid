import { RuleTester } from "eslint";
import { describe, it } from "vitest";
import { importBoundaries } from "../../scripts/import-boundaries";

RuleTester.describe = describe;
RuleTester.it = it;
const tester = new RuleTester({ languageOptions: { ecmaVersion: "latest", sourceType: "module" } });
const ui = "/workspace/web/src/ui/svelte/Button.svelte";
const feature = "/workspace/web/src/features/builds/Editor.svelte";

tester.run("frontend import boundaries", importBoundaries, {
  valid: [
    {
      filename: "/workspace/web/src/ui/svelte/Dialog.svelte",
      code: 'import { Dialog } from "bits-ui";',
    },
    { filename: ui, code: 'import { Field } from "./Field";' },
    { filename: feature, code: 'import { Button } from "../../ui/svelte/Button.svelte";' },
    { filename: "/workspace/web/src/features/builds/server.ts", code: 'import fs from "node:fs";' },
  ],
  invalid: [
    {
      filename: "/workspace/web/src/features/builds/Editor.svelte",
      code: 'import { Dialog } from "bits-ui";',
      errors: [{ messageId: "primitives" }],
    },
    {
      filename: "/workspace/web/src/features/builds/Editor.svelte",
      code: 'import("./server");',
      errors: [{ messageId: "server" }],
    },

    {
      filename: ui,
      code: 'import { api } from "../../features/builds/api";',
      errors: [{ messageId: "ui" }],
    },
    { filename: ui, code: 'export * from "../../lib/i18n";', errors: [{ messageId: "ui" }] },
    { filename: ui, code: 'import("../../generated/sdk.gen");', errors: [{ messageId: "ui" }] },
    {
      filename: feature,
      code: 'import { loader } from "./server";',
      errors: [{ messageId: "server" }],
    },
    { filename: feature, code: 'import("./api.server");', errors: [{ messageId: "server" }] },
    { filename: feature, code: 'export * from "node:fs";', errors: [{ messageId: "server" }] },
    {
      filename: ui,
      code: 'import { api } from "./../../../src/lib/api";',
      errors: [{ messageId: "ui" }],
    },
  ],
});
