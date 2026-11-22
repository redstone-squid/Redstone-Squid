import "@testing-library/jest-dom/vitest";

// jsdom does not implement layout, so scrolling an option into view is a no-op.
Element.prototype.scrollIntoView = () => undefined;
