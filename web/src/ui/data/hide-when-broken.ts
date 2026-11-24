import type { Attachment } from "svelte/attachments";

/**
 * Hide a hydrated image that fails to load so its component shows the fallback.
 *
 * Statically rendered images are handled by `src/ui/image-fallback.ts` instead. An `onerror`
 * handler would do the same job, but Svelte renders it as an inline attribute that the content
 * security policy blocks.
 */
export const hideWhenBroken: Attachment<HTMLImageElement> = (image) => {
  const hide = () => {
    image.hidden = true;
  };
  // The image may have failed before the island hydrated.
  if (image.complete && image.naturalWidth === 0) hide();
  image.addEventListener("error", hide);
  return () => image.removeEventListener("error", hide);
};
