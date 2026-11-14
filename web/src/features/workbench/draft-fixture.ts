export const versionOptions = ["Java 1.21.4", "Java 1.20.6", "Bedrock 1.21.50"] as const;
export type DraftInput = {
  title: string;
  category: "door" | "utility";
  width: string;
  version: string;
};
export type DraftErrors = Partial<
  Record<"title" | "width" | "version", "required" | "positive" | "reserved">
>;
export type SaveResult = { kind: "saved" } | { kind: "invalid"; errors: DraftErrors };

export function validateDraft(input: DraftInput): DraftErrors {
  return {
    ...(!input.title.trim() ? { title: "required" as const } : {}),
    ...(input.category === "door" &&
    (!Number.isInteger(Number(input.width)) || Number(input.width) <= 0)
      ? { width: "positive" as const }
      : {}),
    ...(!versionOptions.some((version) => version === input.version)
      ? { version: "required" as const }
      : {}),
  };
}

function delay(signal: AbortSignal, milliseconds: number): Promise<void> {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    function abort(): void {
      clearTimeout(timer);
      reject(new DOMException("Cancelled", "AbortError"));
    }
    signal.addEventListener("abort", abort, { once: true });
  });
}

/** Deterministic transport simulation shared by both framework trials. No data leaves the browser. */
export function createDraftFixture(retryFirstSave: boolean) {
  let attempts = 0;
  return {
    async save(input: DraftInput, signal: AbortSignal): Promise<SaveResult> {
      await delay(signal, 180);
      if (retryFirstSave && attempts++ === 0) throw new Error("Fixture connection failure");
      if (input.title.trim().toLowerCase() === "reserved")
        return { kind: "invalid", errors: { title: "reserved" } };
      return { kind: "saved" };
    },
    async upload(
      file: File,
      signal: AbortSignal,
      onProgress: (percent: number) => void,
    ): Promise<string> {
      for (const percent of [20, 40, 60, 80, 100]) {
        await delay(signal, 80);
        onProgress(percent);
      }
      return file.name;
    },
  };
}
