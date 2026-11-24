import type { APIRoute } from "astro";
import { writeThemeCookie } from "@/server/cookies";
import { parseTheme } from "@/ui/themes";

/**
 * Save the theme a visitor chose and send them back to the page they were on.
 *
 * The form posts `theme` (`light`, `dark`, or anything else to follow the device) and
 * `return`, the page to go back to. Only paths on this site are followed; anything else goes
 * home. Astro's origin check rejects cross-site posts.
 */
export const POST: APIRoute = async (context) => {
  const form = await context.request.formData();
  const field = (name: string) => {
    const value = form.get(name);
    return typeof value === "string" ? value : undefined;
  };
  writeThemeCookie(context, parseTheme(field("theme")));
  const { url } = context;
  const target = new URL(field("return") ?? "/", url);
  return context.redirect(
    target.origin === url.origin ? `${target.pathname}${target.search}` : "/",
    303,
  );
};
