// Hides images that fail to load so `Image` and `Avatar` show their fallback. Svelte only
// replays an image's error event when a component hydrates, and most pages render images
// statically, so the layouts load this script instead of relying on component handlers.

function hide(target: EventTarget | null): void {
  if (target instanceof HTMLImageElement && target.dataset.fallback !== undefined) {
    target.hidden = true;
  }
}

// Error events do not bubble, so listen in the capture phase.
document.addEventListener("error", (event) => hide(event.target), true);
// Images that failed before this script ran.
for (const image of document.querySelectorAll<HTMLImageElement>("img[data-fallback]")) {
  if (image.complete && image.naturalWidth === 0) hide(image);
}
