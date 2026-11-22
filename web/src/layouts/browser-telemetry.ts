const navigationTiming = performance.getEntriesByType("navigation")[0];

if (
  typeof PerformanceNavigationTiming !== "undefined" &&
  navigationTiming instanceof PerformanceNavigationTiming &&
  Math.random() < 0.1
) {
  const traceparent = navigationTiming.serverTiming.find(
    ({ name }) => name === "squid",
  )?.description;
  if (traceparent !== undefined) {
    const report = (signal: object) => {
      void fetch("/telemetry/browser", {
        method: "POST",
        body: JSON.stringify({ traceparent, ...signal }),
        headers: { "Content-Type": "application/json" },
        credentials: "omit",
        referrerPolicy: "no-referrer",
        keepalive: true,
      }).catch(() => undefined);
    };

    const reportNavigation = () =>
      report({
        kind: "navigation",
        responseStart: Math.round(navigationTiming.responseStart),
        domComplete: Math.round(navigationTiming.domComplete),
      });

    if (document.readyState === "complete") reportNavigation();
    else window.addEventListener("load", reportNavigation, { once: true });

    const reportError = (event: Event) => {
      window.removeEventListener("error", reportError);
      window.removeEventListener("unhandledrejection", reportError);
      report({ kind: event.type === "error" ? "script_error" : "unhandled_rejection" });
    };
    window.addEventListener("error", reportError);
    window.addEventListener("unhandledrejection", reportError);
  }
}
