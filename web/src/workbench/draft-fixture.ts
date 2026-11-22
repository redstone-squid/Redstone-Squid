import * as m from "../locales/messages";

export const versionOptions = ["Java 1.21.4", "Java 1.20.6", "Bedrock 1.21.50"] as const;
export type DraftInput = {
  title: string;
  category: "door" | "utility";
  width: string;
  version: string;
};
type DraftError = "required" | "positive" | "reserved";
export type DraftErrors = Partial<Record<"title" | "width" | "version", DraftError>>;
export const draftErrorMessages = {
  required: m.trialRequired,
  positive: m.trialPositive,
  reserved: m.trialReserved,
} as const satisfies Record<DraftError, unknown>;
export type DraftScenario = "retry" | "upload-retry";
export type DraftPhase = "idle" | "saving" | "saved" | "uploading" | "error";

const phaseMessages = {
  idle: { tone: "info", message: m.trialReady },
  saving: { tone: "info", message: m.trialSaving },
  saved: { tone: "success", message: m.trialSaved },
  uploading: { tone: "info", message: m.trialUploading },
  error: { tone: "warning", message: m.trialFailed },
} as const;

export function draftFeedback(phase: DraftPhase, errors: DraftErrors) {
  if (phase === "idle" && Object.keys(errors).length > 0) {
    return { tone: "warning", message: m.trialFixErrors } as const;
  }
  return phaseMessages[phase];
}

export type SaveResult = { kind: "saved" } | { kind: "invalid"; errors: DraftErrors };

export function validateDraft(input: DraftInput): DraftErrors {
  return {
    ...(input.title.trim() === "" ? { title: "required" as const } : {}),
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

/** Deterministic transport simulation for the draft trial. No data leaves the browser. */
export function createDraftFixture(scenario?: DraftScenario) {
  let failedOnce = false;
  return {
    async save(input: DraftInput, signal: AbortSignal): Promise<SaveResult> {
      await delay(signal, 180);
      if (scenario === "retry" && !failedOnce) {
        failedOnce = true;
        throw new Error("Fixture connection failure");
      }
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
        if (scenario === "upload-retry" && percent === 40 && !failedOnce) {
          failedOnce = true;
          throw new Error("Fixture upload failure");
        }
        onProgress(percent);
      }
      return file.name;
    },
  };
}
