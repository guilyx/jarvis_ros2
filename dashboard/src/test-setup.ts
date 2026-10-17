import "@testing-library/jest-dom/vitest";

// jsdom has no layout: charts and virtual lists only need these to exist.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
window.scrollTo = () => {};
