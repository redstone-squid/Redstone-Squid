import type { Rule } from "eslint";

// Elements src/ui wraps with their states, labels, and themes.
const WRAPPED = {
  button: "actions/Button",
  input: "a forms field",
  select: "forms/SelectField",
  textarea: "forms/TextareaField",
  table: "table/DataTable",
  dialog: "overlays/Dialog",
  img: "data/Image",
} as const;

/** The parts of Svelte and Astro (JSX) element nodes this rule reads. */
type ElementNode = {
  kind?: string;
  name?: { name?: unknown };
  startTag?: { attributes?: Attribute[] };
  attributes?: Attribute[];
};
type Attribute = { key?: { name?: unknown }; name?: { name?: unknown }; value?: unknown };

function isWrapped(name: unknown): name is keyof typeof WRAPPED {
  return typeof name === "string" && Object.hasOwn(WRAPPED, name);
}

/** Whether the element has a literal `type="hidden"`, which renders nothing to style. */
function isHiddenInput(attributes: Attribute[]): boolean {
  return attributes.some((attribute) => {
    if ((attribute.key?.name ?? attribute.name?.name) !== "type") return false;
    const parts = Array.isArray(attribute.value) ? attribute.value : [attribute.value];
    return parts.some(
      (part: unknown) =>
        typeof part === "object" && part !== null && "value" in part && part.value === "hidden",
    );
  });
}

/**
 * Report native controls, tables, dialogs, and images in Svelte or Astro markup, which must
 * use the src/ui component that wraps them. Hidden inputs are allowed. Disable the rule on a
 * line with a reason when no component fits.
 */
export const noRawElements: Rule.RuleModule = {
  meta: {
    type: "problem",
    schema: [],
    messages: {
      element: "Use {{component}} from src/ui instead of a raw <{{name}}>.",
    },
  },
  create(context) {
    function check(node: Rule.Node, element: ElementNode, attributes: Attribute[]): void {
      const name = element.name?.name;
      if (!isWrapped(name) || (name === "input" && isHiddenInput(attributes))) return;
      context.report({ node, messageId: "element", data: { name, component: WRAPPED[name] } });
    }
    return {
      SvelteElement(node: Rule.Node) {
        const element = node as ElementNode;
        if (element.kind === "html") check(node, element, element.startTag?.attributes ?? []);
      },
      JSXOpeningElement(node: Rule.Node) {
        const element = node as ElementNode;
        check(node, element, element.attributes ?? []);
      },
    };
  },
};
