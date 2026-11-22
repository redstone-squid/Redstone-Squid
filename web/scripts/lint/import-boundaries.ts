import path from "node:path";
import type { Rule } from "eslint";

/**
 * Which top-level directories under `src/` each directory may import from.
 *
 * Files directly in `src/` (middleware) may import anything. A feature may import its own
 * directory but not another feature.
 */
const LAYERS = {
  pages: ["pages", "layouts", "features", "workbench", "server", "ui", "locales", "generated"],
  layouts: ["layouts", "ui", "locales", "generated"],
  features: ["ui", "locales", "generated"],
  workbench: ["workbench", "layouts", "features", "ui", "locales", "generated"],
  server: ["server", "locales", "generated"],
  locales: ["locales", "generated"],
  ui: ["ui"],
} as const satisfies Record<string, readonly string[]>;
type Layer = keyof typeof LAYERS;

const LAYER_MESSAGES = {
  layer: "See web/CLAUDE.md for the layer map.",
  serverLayer:
    "Only pages and middleware call the API. Load data in the page and pass it as props.",
  featureLayer:
    "Features stay independent. Move shared presentation to src/ui, or combine features in a page.",
  workbenchLayer: "The workbench is development tooling; production code cannot depend on it.",
  uiLayer: "Shared UI cannot depend on application code. Pass data and labels through props.",
};

function isLayer(name: string | undefined): name is Layer {
  return name !== undefined && Object.hasOwn(LAYERS, name);
}

/** Return the `src/` directory and the path segments below it, or undefined outside `src/`. */
function locate(file: string): { src: string; segments: string[] } | undefined {
  const index = file.lastIndexOf("/src/");
  if (index === -1) return undefined;
  return { src: file.slice(0, index + 5), segments: file.slice(index + 5).split("/") };
}

// Libraries that only src/ui wraps, so features get one accessible, themed version of each.
const UI_ONLY = /^(?:bits-ui|layerchart|@tanstack\/svelte-table)(?:\/|$)/;

/** Enforce the `LAYERS` table, keep `UI_ONLY` libraries inside src/ui, and keep server code out of the browser. */
export const importBoundaries: Rule.RuleModule = {
  meta: {
    type: "problem",
    schema: [],
    messages: {
      ...Object.fromEntries(
        Object.entries(LAYER_MESSAGES).map(([id, reason]) => [
          id,
          `src/{{from}} cannot import src/{{to}}. ${reason}`,
        ]),
      ),
      primitives:
        "Import the src/ui component that wraps this library; only src/ui uses it directly.",
      server: "Browser code cannot import server modules or Node built-ins.",
    },
  },
  create(context) {
    const filename = context.filename.replaceAll("\\", "/");
    const self = locate(filename);
    const from = self && self.segments.length > 1 ? self.segments[0] : undefined;
    const isBrowser = filename.endsWith(".svelte") || /\.astro\/[^/]+\.[jt]s$/.test(filename);

    function check(node: Rule.Node, source: unknown): void {
      if (
        typeof source !== "object" ||
        source === null ||
        !("value" in source) ||
        typeof source.value !== "string"
      )
        return;
      const imported = source.value;
      if (from !== "ui" && UI_ONLY.test(imported)) {
        context.report({ node, messageId: "primitives" });
      }
      if (isBrowser && imported.startsWith("node:")) {
        context.report({ node, messageId: "server" });
      }
      if (!self) return;
      // `@/` is the tsconfig alias for src/.
      const absolute = imported.startsWith("@/")
        ? `${self.src}${imported.slice(2)}`
        : imported.startsWith(".")
          ? path.resolve(path.dirname(filename), imported).replaceAll("\\", "/")
          : undefined;
      if (absolute === undefined) return;
      const target = locate(absolute);
      if (target?.src !== self.src) return;
      const to = target.segments[0];
      if (isBrowser && to === "server") {
        context.report({ node, messageId: "server" });
        return;
      }
      if (!isLayer(from) || to === undefined) return;
      const allowed: readonly string[] = LAYERS[from];
      const ownFeature =
        from === "features" && to === "features" && self.segments[1] === target.segments[1];
      if (allowed.includes(to) || ownFeature) return;
      const messageId: keyof typeof LAYER_MESSAGES =
        from === "ui"
          ? "uiLayer"
          : to === "server"
            ? "serverLayer"
            : to === "features"
              ? "featureLayer"
              : to === "workbench"
                ? "workbenchLayer"
                : "layer";
      context.report({ node, messageId, data: { from, to } });
    }

    return {
      ImportDeclaration(node) {
        check(node, node.source);
      },
      ExportNamedDeclaration(node) {
        check(node, node.source);
      },
      ExportAllDeclaration(node) {
        check(node, node.source);
      },
      ImportExpression(node) {
        check(node, node.source);
      },
    };
  },
};
