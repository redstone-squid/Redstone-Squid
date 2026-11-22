import type { Locale } from "@/locales/locale";

/** Story options keyed by their query-parameter value, each with its label message. */
export type StoryOptionLabels<T extends string> = Record<T, (locale: Locale) => string>;

/** Return the option a query parameter selects, or `fallback` when it names none of them. */
export function selectedOption<T extends string>(
  options: StoryOptionLabels<T>,
  params: URLSearchParams,
  parameter: string,
  fallback: NoInfer<T>,
): T {
  const value = params.get(parameter);
  return (Object.keys(options) as T[]).find((option) => option === value) ?? fallback;
}
