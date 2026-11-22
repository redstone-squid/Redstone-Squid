FROM oven/bun:1.4.4 AS bun

FROM node:24-bookworm-slim AS build

# The PWA build uses node:module.findPackageJSON, which Bun 1.4.4 lacks.
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
WORKDIR /app
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile
COPY contracts/vocabulary.json /contracts/vocabulary.json
COPY web/ .
RUN bun run build

FROM bun AS runtime

ENV HOST=0.0.0.0 \
    NODE_ENV=production \
    PORT=4321
WORKDIR /app
COPY --from=build --chown=bun:bun /app/dist ./dist
COPY --from=build --chown=bun:bun /app/node_modules ./node_modules
COPY --from=build --chown=bun:bun /app/package.json ./package.json
USER bun
EXPOSE 4321
CMD ["bun", "dist/server/entry.mjs"]
