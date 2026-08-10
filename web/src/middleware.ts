import { defineMiddleware } from "astro:middleware";

export const onRequest = defineMiddleware(async (context, next) => {
  const startedAt = performance.now();
  try {
    const renderedResponse = await next();
    const body = context.request.method === "HEAD" ? null : await renderedResponse.arrayBuffer();
    const response = new Response(body, {
      status: context.locals.responseStatus ?? renderedResponse.status,
      statusText: context.locals.responseStatus ? undefined : renderedResponse.statusText,
      headers: renderedResponse.headers,
    });
    console.info(
      JSON.stringify({
        event: "http_request",
        method: context.request.method,
        path: context.url.pathname,
        status: response.status,
        duration_ms: Math.round(performance.now() - startedAt),
      }),
    );
    return response;
  } catch (error) {
    console.error(
      JSON.stringify({
        event: "http_request_error",
        method: context.request.method,
        path: context.url.pathname,
        duration_ms: Math.round(performance.now() - startedAt),
        error: error instanceof Error ? error.message : String(error),
      }),
    );
    throw error;
  }
});
