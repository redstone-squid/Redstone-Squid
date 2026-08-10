import type { BuildSummary, BuildTag, Dimensions } from "../generated/types.gen";
import { formatNumber, translate, translateTaxonomy, type Locale } from "./i18n";

export type MediaItem = { kind: "render" | "image"; url: string; label: string };

export function safeHttpsUrl(value: string): string | undefined {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.toString() : undefined;
  } catch {
    return undefined;
  }
}

export function formatDimensions(locale: Locale, dimensions: Dimensions): string {
  const values = [dimensions.width, dimensions.height, dimensions.depth];
  if (values.every((value) => value === null)) return translate(locale, "common.notAvailable");
  return values.map((value) => (value === null ? "?" : formatNumber(locale, value))).join(" × ");
}

export function formatTiming(locale: Locale, value: number | null): string {
  return value === null
    ? translate(locale, "common.notAvailable")
    : `${formatNumber(locale, value)} ${translateTaxonomy(locale, "gt")}`;
}

export function formatTag(locale: Locale, tag: BuildTag): string {
  const name = translateTaxonomy(locale, tag.key, tag.name);
  if (tag.value === null) return name;
  const value =
    typeof tag.value === "boolean"
      ? translate(locale, tag.value ? "common.true" : "common.false")
      : tag.value;
  return `${name}: ${value}${tag.unit ? ` ${translateTaxonomy(locale, tag.unit, tag.unit)}` : ""}`;
}

export function buildMedia(build: { links: { renders: string[]; images: string[] } }): MediaItem[] {
  return [
    ...build.links.renders.map((url, index) => ({
      kind: "render" as const,
      url,
      label: `Render ${index + 1}`,
    })),
    ...build.links.images.map((url, index) => ({
      kind: "image" as const,
      url,
      label: `Image ${index + 1}`,
    })),
  ].filter((item): item is MediaItem => safeHttpsUrl(item.url) !== undefined);
}

export function cardPreview(build: BuildSummary): string | undefined {
  return build.preview ? safeHttpsUrl(build.preview.url) : undefined;
}

export function externalLinks(values: string[]): string[] {
  return values.flatMap((value) => {
    const url = safeHttpsUrl(value);
    return url ? [url] : [];
  });
}
