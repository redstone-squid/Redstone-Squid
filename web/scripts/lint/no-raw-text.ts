import type { Rule } from "eslint";

// Attributes that browsers show or announce to users.
const VISIBLE_ATTRIBUTES = new Set([
  "alt",
  "aria-description",
  "aria-label",
  "placeholder",
  "title",
]);
const LETTERS = /\p{L}/u;

/** The parts of Svelte and Astro (JSX) template nodes this rule reads. */
type TemplateNode = {
  type: string;
  parent?: TemplateNode;
  value?: unknown;
  key?: { name?: unknown };
  name?: { name?: unknown };
  startTag?: { attributes?: TemplateNode[] };
  openingElement?: { attributes?: TemplateNode[] };
};

function attributeName(attribute: TemplateNode): unknown {
  return attribute.key?.name ?? attribute.name?.name;
}

/** Join the literal parts of an attribute value, skipping expressions. */
function literalText(attribute: TemplateNode): string {
  // Svelte stores attribute values as a list of literal and mustache parts.
  const parts = (Array.isArray(attribute.value) ? attribute.value : [attribute.value]) as unknown[];
  return parts
    .map((part) =>
      typeof part === "object" &&
      part !== null &&
      "type" in part &&
      (part.type === "SvelteLiteral" || part.type === "Literal") &&
      "value" in part
        ? String(part.value)
        : "",
    )
    .join("");
}

function isHidden(node: TemplateNode): boolean {
  for (let element = node.parent; element; element = element.parent) {
    const attributes = element.startTag?.attributes ?? element.openingElement?.attributes ?? [];
    if (
      attributes.some(
        (attribute) =>
          attributeName(attribute) === "aria-hidden" && literalText(attribute) === "true",
      )
    )
      return true;
  }
  return false;
}

/**
 * Report letters written directly in Svelte or Astro markup.
 *
 * Text a user sees belongs in the Fluent catalogs so both locales stay complete. Punctuation,
 * symbols, numbers, and content under `aria-hidden="true"` are allowed.
 */
export const noRawText: Rule.RuleModule = {
  meta: {
    type: "problem",
    schema: [],
    messages: {
      text: "Move user-visible text to src/locales/*/messages.ftl and call it through `m` from @/locales/messages.gen.",
    },
  },
  create(context) {
    function report(node: Rule.Node, text: unknown): void {
      if (typeof text === "string" && LETTERS.test(text) && !isHidden(node as TemplateNode))
        context.report({ node, messageId: "text" });
    }
    function checkAttribute(node: Rule.Node): void {
      const attribute = node as TemplateNode;
      const name = attributeName(attribute);
      if (typeof name === "string" && VISIBLE_ATTRIBUTES.has(name))
        report(node, literalText(attribute));
    }
    function checkText(node: Rule.Node): void {
      report(node, (node as TemplateNode).value);
    }
    return {
      SvelteText: checkText,
      JSXText: checkText,
      SvelteAttribute: checkAttribute,
      JSXAttribute: checkAttribute,
    };
  },
};
