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
