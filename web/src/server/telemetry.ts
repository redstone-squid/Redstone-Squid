import { SpanKind, SpanStatusCode, trace } from "@opentelemetry/api";
import { NodeSDK } from "@opentelemetry/sdk-node";

if (process.env.SQUID_OBSERVABILITY_ENABLED === "true") {
  new NodeSDK({
    serviceName: process.env.OTEL_SERVICE_NAME ?? "redstone-squid-web",
    autoDetectResources: false,
    logRecordProcessors: [],
    metricReaders: [],
  }).start();
}

const tracer = trace.getTracer("redstone-squid-web");

/** Trace an HTTP operation using low-cardinality attributes and no request payloads. */
export function traceHttp(
  name: string,
  kind: SpanKind,
  attributes: Record<string, string>,
  operation: () => Promise<Response>,
): Promise<Response> {
  return tracer.startActiveSpan(name, { kind, attributes }, async (span) => {
    try {
      const response = await operation();
      span.setAttribute("http.response.status_code", response.status);
      if (response.status >= (kind === SpanKind.CLIENT ? 400 : 500)) {
        span.setStatus({ code: SpanStatusCode.ERROR });
      }
      return response;
    } catch (error) {
      span.setStatus({ code: SpanStatusCode.ERROR });
      throw error;
    } finally {
      span.end();
    }
  });
}
