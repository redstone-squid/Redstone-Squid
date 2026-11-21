import { takeSelectedFiles, type FilePickerProps } from "../upload";

export function FilePicker({ label, onFiles, accept, multiple, disabled }: FilePickerProps) {
  return (
    <label className="sq-file-picker">
      <span>{label}</span>
      <input
        type="file"
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        onChange={(event) => {
          const files = takeSelectedFiles(event.currentTarget);
          if (files.length) onFiles(files);
        }}
      />
    </label>
  );
}
