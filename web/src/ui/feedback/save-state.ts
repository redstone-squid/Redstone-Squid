/** Where an autosaving form or setting stands; `SaveStatus` shows it. */
export type SaveState = "idle" | "dirty" | "saving" | "saved" | "offline" | "error" | "conflict";
