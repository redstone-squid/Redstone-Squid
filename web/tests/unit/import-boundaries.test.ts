import { RuleTester } from "eslint";
import { describe, it } from "vitest";
import { importBoundaries } from "../../scripts/lint/import-boundaries";

RuleTester.describe = describe;
RuleTester.it = it;
const tester = new RuleTester({ languageOptions: { ecmaVersion: "latest", sourceType: "module" } });
const src = "/workspace/web/src";
const ui = `${src}/ui/svelte/Button.svelte`;
const feature = `${src}/features/builds/Editor.svelte`;
const featureModule = `${src}/features/builds/editor.ts`;
const page = `${src}/pages/builds/index.astro`;

tester.run("frontend import boundaries", importBoundaries, {
  valid: [
    { filename: `${src}/ui/svelte/Dialog.svelte`, code: 'import { Dialog } from "bits-ui";' },
    { filename: ui, code: 'import { Field } from "./Field";' },
    { filename: feature, code: 'import { Button } from "../../ui/svelte/Button.svelte";' },
    { filename: feature, code: 'import { draft } from "./draft";' },
    { filename: featureModule, code: 'import * as m from "@/locales/messages.gen";' },
    { filename: page, code: 'import { unwrap } from "../../server/api";' },
    { filename: page, code: 'import { unwrap } from "@/server/api";' },
    { filename: page, code: 'import Editor from "../../features/builds/Editor.svelte";' },
    { filename: `${src}/middleware.ts`, code: 'import { createApiClient } from "./server/api";' },
    {
      filename: `${src}/workbench/Story.astro`,
      code: 'import X from "../features/builds/X.astro";',
    },
    {
      filename: `${src}/server/api.ts`,
      code: 'import { createClient } from "../generated/client";',
    },
  ],
  invalid: [
    {
      filename: feature,
      code: 'import { Dialog } from "bits-ui";',
      errors: [{ messageId: "primitives" }],
    },
    { filename: feature, code: 'import("../../server/api");', errors: [{ messageId: "server" }] },
    {
      filename: feature,
      code: 'import { unwrap } from "@/server/api";',
      errors: [{ messageId: "server" }],
    },
    {
      filename: featureModule,
      code: 'import { x } from "@/features/records/x";',
      errors: [{ messageId: "featureLayer", data: { from: "features", to: "features" } }],
    },
    { filename: feature, code: 'export * from "node:fs";', errors: [{ messageId: "server" }] },
    {
      filename: featureModule,
      code: 'import { unwrap } from "../../server/api";',
      errors: [{ messageId: "serverLayer", data: { from: "features", to: "server" } }],
    },
    {
      filename: featureModule,
      code: 'import { x } from "../records/x";',
      errors: [{ messageId: "featureLayer", data: { from: "features", to: "features" } }],
    },
    {
      filename: featureModule,
      code: 'import { fixtures } from "../../workbench/fixtures";',
      errors: [{ messageId: "workbenchLayer", data: { from: "features", to: "workbench" } }],
    },
    {
      filename: `${src}/layouts/Shell.astro`,
      code: 'import { getRuntimeConfig } from "../server/config";',
      errors: [{ messageId: "serverLayer", data: { from: "layouts", to: "server" } }],
    },
    {
      filename: `${src}/lib/format.ts`,
      code: 'import { buttonStyles } from "../ui/variants";',
      errors: [{ messageId: "layer", data: { from: "lib", to: "ui" } }],
    },
    {
      filename: ui,
      code: 'import { api } from "../../features/builds/api";',
      errors: [{ messageId: "uiLayer", data: { from: "ui", to: "features" } }],
    },
    {
      filename: ui,
      code: 'export * from "../../lib/i18n";',
      errors: [{ messageId: "uiLayer", data: { from: "ui", to: "lib" } }],
    },
    {
      filename: ui,
      code: 'import("../../generated/sdk.gen");',
      errors: [{ messageId: "uiLayer", data: { from: "ui", to: "generated" } }],
    },
  ],
});
