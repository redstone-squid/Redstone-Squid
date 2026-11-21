import type { ProgressProps } from "../upload";

export function Progress({ label, value, max = 100 }: ProgressProps) {
  return (
    <label className="sq-progress">
      <span>{label}</span>
      <progress value={value} max={max} />
    </label>
  );
}
