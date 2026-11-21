export type FilePickerProps = {
  label: string;
  accept?: string;
  multiple?: boolean;
  disabled?: boolean;
  onFiles: (files: File[]) => void;
};

/** Take a snapshot before resetting the native picker so the same file can be selected again. */
export function takeSelectedFiles(input: HTMLInputElement): File[] {
  const selection = Array.from(input.files ?? []);
  input.value = "";
  return selection;
}

export type ProgressProps = {
  label: string;
  /** Omit the value for indeterminate progress. */
  value?: number;
  max?: number;
};
