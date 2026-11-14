import path from "node:path";
import type { Rule } from "eslint";

/** Keep shared UI independent and server dependencies out of browser components. */
export const importBoundaries: Rule.RuleModule = {
  meta: {
    type: "problem",
    schema: [],
    messages: {
      ui: "Shared UI cannot import application code. Pass data and labels through component props.",
      radix: "Import the shared UI wrapper instead. Radix primitives belong inside src/ui.",
      server: "Browser components cannot import server modules. Use a browser API adapter.",
    },
  },
  create(context) {
    const filename = context.filename.replaceAll("\\", "/");
    const isUi = filename.includes("/src/ui/");
    const isBrowser = filename.endsWith(".tsx") || /\.astro\/[^/]+\.[jt]s$/.test(filename);
    function check(node: Rule.Node, source: unknown): void {
      if (
        typeof source !== "object" ||
        source === null ||
        !("value" in source) ||
        typeof source.value !== "string"
      )
        return;
      const imported = source.value;
      const resolved = imported.startsWith(".")
        ? path.resolve(path.dirname(filename), imported).replaceAll("\\", "/")
        : imported;
      if (
        isUi &&
        /(?:^|\/)(?:features|lib|generated|platform|pages|views)(?:\/|$)/.test(resolved)
      ) {
        context.report({ node, messageId: "ui" });
      }
      if (!isUi && /^(?:radix-ui(?:\/|$)|@radix-ui\/)/.test(imported)) {
        context.report({ node, messageId: "radix" });
      }
      if (
        isBrowser &&
        (/(?:^|\/)server(?:[./]|$)|\.server(?:[./]|$)/.test(resolved) ||
          imported.startsWith("node:"))
      ) {
        context.report({ node, messageId: "server" });
      }
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
