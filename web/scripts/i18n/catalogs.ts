import {
  parse,
  Visitor,
  type MessageReference,
  type TermReference,
  type VariableReference,
  type Pattern,
} from "@fluent/syntax";

type Reference = { id: string; inheritVariables: boolean };
class Dependencies extends Visitor {
  readonly variables = new Set<string>();
  readonly references: Reference[] = [];
  override visitVariableReference(node: VariableReference): void {
    this.variables.add(node.id.name);
  }
  override visitMessageReference(node: MessageReference): void {
    this.references.push({
      id: node.id.name + (node.attribute ? `.${node.attribute.name}` : ""),
      inheritVariables: true,
    });
  }
  override visitTermReference(node: TermReference): void {
    this.references.push({
      id: `-${node.id.name}${node.attribute ? `.${node.attribute.name}` : ""}`,
      inheritVariables: false,
    });
    // Terms have their own argument scope; expressions in call arguments still belong to the caller.
    this.genericVisit(node);
  }
}

export function catalogContract(source: string): Record<string, string[]> {
  const patterns = new Map<string, Pattern>();
  const messages: string[] = [];
  for (const entry of parse(source, {}).body) {
    if (entry.type === "Junk")
      throw new Error(
        `Invalid Fluent: ${entry.annotations.map((item) => item.message).join(", ")}`,
      );
    if (entry.type !== "Message" && entry.type !== "Term") continue;
    const id = `${entry.type === "Term" ? "-" : ""}${entry.id.name}`;
    if (!/^-?[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(id))
      throw new Error(`Message IDs use lowercase kebab-case: ${id}`);
    if (!entry.value) throw new Error(`Empty message: ${id}`);
    if (patterns.has(id)) throw new Error(`Duplicate message: ${id}`);
    patterns.set(id, entry.value);
    for (const attribute of entry.attributes) {
      const key = `${id}.${attribute.id.name}`;
      if (patterns.has(key)) throw new Error(`Duplicate attribute: ${key}`);
      patterns.set(key, attribute.value);
    }
    if (entry.type === "Message") messages.push(id);
  }
  const resolved = new Map<string, string[]>();
  function resolve(id: string, ancestors: string[] = []): string[] {
    if (ancestors.includes(id))
      throw new Error(`Cyclic Fluent reference: ${[...ancestors, id].join(" -> ")}`);
    const cached = resolved.get(id);
    if (cached) return cached;
    const pattern = patterns.get(id);
    if (!pattern) throw new Error(`Unknown Fluent reference: ${id}`);
    const dependencies = new Dependencies();
    dependencies.visit(pattern);
    for (const reference of dependencies.references) {
      const variables = resolve(reference.id, [...ancestors, id]);
      if (reference.inheritVariables)
        for (const variable of variables) dependencies.variables.add(variable);
    }
    const variables = [...dependencies.variables].sort();
    resolved.set(id, variables);
    return variables;
  }
  // Validate even unused terms and attributes so broken references cannot hide in a catalog.
  for (const id of patterns.keys()) resolve(id);
  return Object.fromEntries(
    messages.sort((a, b) => a.localeCompare(b, "en")).map((id) => [id, resolve(id)]),
  );
}

export function compareCatalogs(
  reference: Record<string, string[]>,
  translated: Record<string, string[]>,
): void {
  const errors: string[] = [];
  for (const id of new Set([...Object.keys(reference), ...Object.keys(translated)])) {
    if (!Object.hasOwn(reference, id)) errors.push(`unexpected message ${id}`);
    else if (!Object.hasOwn(translated, id)) errors.push(`missing message ${id}`);
    else if (JSON.stringify(reference[id]) !== JSON.stringify(translated[id])) {
      errors.push(
        `${id}: expected variables ${JSON.stringify(reference[id])}, received ${JSON.stringify(translated[id])}`,
      );
    }
  }
  if (errors.length > 0)
    throw new Error(`Catalog IDs and variables must match English:\n${errors.join("\n")}`);
}
