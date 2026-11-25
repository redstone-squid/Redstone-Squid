import type { APIRoute } from "astro";
import { returnPath, signOut } from "@/server/session";

/**
 * Sign the visitor out and send them back to `return`, the page they were on. The header's
 * account menu posts here; Astro's origin check rejects cross-site posts.
 */
export const POST: APIRoute = async (context) => {
  await signOut(context);
  return context.redirect(returnPath(context.url.searchParams.get("return")), 303);
};
