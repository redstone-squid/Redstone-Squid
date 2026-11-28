class FinalizationStatus extends HTMLElement {
  private controller?: AbortController;
  private timer?: number;
  private delay = 2000;
  private polling = false;
  connectedCallback() {
    this.controller = new AbortController();
    document.addEventListener("visibilitychange", this.schedule, {
      signal: this.controller.signal,
    });
    this.schedule();
  }
  disconnectedCallback() {
    this.controller?.abort();
    window.clearTimeout(this.timer);
  }
  private readonly schedule = () => {
    window.clearTimeout(this.timer);
    if (!document.hidden && !this.controller?.signal.aborted)
      this.timer = window.setTimeout(() => void this.poll(), this.delay);
  };
  private async poll() {
    const signal = this.controller?.signal;
    if (this.polling || !signal || this.dataset.url === undefined) return;
    this.polling = true;
    try {
      const response = await fetch(this.dataset.url, {
        headers: this.dataset.etag !== undefined ? { "If-None-Match": this.dataset.etag } : {},
        credentials: "include",
        priority: "low",
        cache: "no-store",
        signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
      });
      if (response.ok || [401, 403, 404].includes(response.status)) {
        location.reload();
        return;
      }
    } catch {
      if (signal.aborted) return;
    } finally {
      this.polling = false;
    }
    this.delay = Math.min(this.delay * 2, 30000);
    this.schedule();
  }
}
customElements.define("finalization-status", FinalizationStatus);
