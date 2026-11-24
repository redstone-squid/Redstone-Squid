import type { APIRoute } from "astro";
import { writePreference } from "@/server/cookies";
import { parseContrast, parseTheme } from "@/ui/themes";

/**
 * Save a display preference and send the visitor back to the page they were on.
 *
 * The form posts `theme` (`light` or `dark`) or `contrast` (`more` or `standard`); any other
 * value, such as `system`, deletes the preference so the page follows the device. `return` is
 * the page to go back to. Only paths on this site are followed; anything else goes home.
 * Astro's origin check rejects cross-site posts.
 */
export const POST: APIRoute = async (context) => {
  const form = await context.request.formData();
  const field = (name: string) => {
    const value = form.get(name);
    return typeof value === "string" ? value : undefined;
  };
  const theme = field("theme");
  if (theme !== undefined) writePreference(context, "theme", parseTheme(theme));
  const contrast = field("contrast");
  if (contrast !== undefined) writePreference(context, "contrast", parseContrast(contrast));
  const { url } = context;
  const target = new URL(field("return") ?? "/", url);
  return context.redirect(
    target.origin === url.origin ? `${target.pathname}${target.search}` : "/",
    303,
  );
};
