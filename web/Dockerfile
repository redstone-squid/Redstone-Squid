FROM oven/bun:1.3.13 AS build

WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile
COPY . .
RUN bun run build

FROM node:24-bookworm-slim AS runtime

ENV HOST=0.0.0.0 \
    NODE_ENV=production \
    PORT=4321
WORKDIR /app
COPY --from=build --chown=node:node /app/dist ./dist
COPY --from=build --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/package.json ./package.json
USER node
EXPOSE 4321
CMD ["node", "dist/server/entry.mjs"]
