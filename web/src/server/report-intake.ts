/**
 * Reads the JSON reports browsers post on their own, such as telemetry and policy violations.
 * Anyone can post to these endpoints, so each intake caps body size and limits how many
 * reports this process accepts per minute.
 */
export class ReportIntake {
  readonly #maxBytes: number;
  readonly #perMinute: number;
  #windowStart = 0;
  #accepted = 0;

  constructor(limits: { maxBytes: number; perMinute: number }) {
    this.#maxBytes = limits.maxBytes;
    this.#perMinute = limits.perMinute;
  }

  /** Return the parsed body, or the response that turns the request away. */
  async read(request: Request): Promise<{ body: unknown } | { rejection: Response }> {
    const now = performance.now();
    if (now - this.#windowStart >= 60_000) {
      this.#windowStart = now;
      this.#accepted = 0;
    }
    if (this.#accepted >= this.#perMinute) return reject(429);
    this.#accepted++;

    const reader = request.body?.getReader();
    if (reader === undefined) return reject(400);
    const bytes = new Uint8Array(this.#maxBytes);
    let length = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (length + value.length > this.#maxBytes) {
        void reader.cancel().catch(() => undefined);
        return reject(413);
      }
      bytes.set(value, length);
      length += value.length;
    }
    try {
      return { body: JSON.parse(new TextDecoder().decode(bytes.subarray(0, length))) };
    } catch {
      return reject(400);
    }
  }
}

function reject(status: number) {
  return { rejection: new Response(null, { status }) };
}
