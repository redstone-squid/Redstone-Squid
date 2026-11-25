import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/svelte";
import { afterEach, beforeEach } from "vitest";

afterEach(cleanup);

// Components read the page locale from <html lang>, like the browser after server rendering.
beforeEach(() => {
  document.documentElement.lang = "en";
});

// jsdom does not implement layout, so scrolling an option into view is a no-op.
Element.prototype.scrollIntoView = () => undefined;

// jsdom has no layout or media queries. LayerChart observes its size and reads
// prefers-reduced-motion when it loads.
window.ResizeObserver = class {
  observe = () => undefined;
  unobserve = () => undefined;
  disconnect = () => undefined;
};
window.matchMedia = (query: string) =>
  ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
    // The type still requires the legacy listener methods.
    // eslint-disable-next-line @typescript-eslint/no-deprecated
    addListener: () => undefined,
    // eslint-disable-next-line @typescript-eslint/no-deprecated
    removeListener: () => undefined,
  }) satisfies MediaQueryList;

// jsdom has no Web Animations, which Svelte's `animate:` directives use. Animations finish at
// once, so components see the same end state as in a browser.
Element.prototype.getAnimations = () => [];
Element.prototype.animate = function animate() {
  const animation = Object.assign(new EventTarget(), {
    onfinish: null as (() => void) | null,
    cancel: () => undefined,
    finish: () => undefined,
    currentTime: 0,
  }) as Animation;
  queueMicrotask(() => {
    animation.onfinish?.call(animation, new Event("finish") as AnimationPlaybackEvent);
  });
  return animation;
};
