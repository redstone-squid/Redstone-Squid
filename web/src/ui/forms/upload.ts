/** Take a snapshot before resetting the native picker so the same file can be chosen again. */
export function takeSelectedFiles(input: HTMLInputElement): File[] {
  const selection = Array.from(input.files ?? []);
  input.value = "";
  return selection;
}

/**
 * Whether a dropped file matches an `accept` attribute (".litematic,image/*"). The picker
 * filters its own choices, but a drop bypasses it.
 */
export function accepts(file: File, accept: string | undefined): boolean {
  const patterns = (accept ?? "")
    .split(",")
    .map((pattern) => pattern.trim().toLowerCase())
    .filter((pattern) => pattern !== "");
  if (patterns.length === 0) return true;
  const name = file.name.toLowerCase();
  const type = file.type.toLowerCase();
  return patterns.some((pattern) =>
    pattern.startsWith(".")
      ? name.endsWith(pattern)
      : pattern.endsWith("/*")
        ? type.startsWith(pattern.slice(0, -1))
        : type === pattern,
  );
}
