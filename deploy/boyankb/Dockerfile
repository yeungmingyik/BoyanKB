FROM node:24.16.0-alpine@sha256:21f403ab171f2dc89bad4dd69d7721bfd15f084ccb46cdd225f31f2bc59b5c9a AS base

RUN apk add --no-cache jemalloc python3 py3-pip uv && npm install --global npm@11.13.0

ENV LD_PRELOAD=/usr/lib/libjemalloc.so.2
ENV SCARF_ANALYTICS=false
ENV HUSKY=0

WORKDIR /app
RUN chown node:node /app
USER node

FROM base AS build

COPY --chown=node:node package.json package-lock.json ./
COPY --chown=node:node api/package.json ./api/package.json
COPY --chown=node:node client/package.json ./client/package.json
COPY --chown=node:node packages/api/package.json ./packages/api/package.json
COPY --chown=node:node packages/client/package.json ./packages/client/package.json
COPY --chown=node:node packages/data-provider/package.json ./packages/data-provider/package.json
COPY --chown=node:node packages/data-schemas/package.json ./packages/data-schemas/package.json

RUN --mount=type=cache,target=/home/node/.npm,uid=1000,gid=1000 npm ci --no-audit --no-fund

COPY --chown=node:node . .

ARG NODE_MAX_OLD_SPACE_SIZE=4096
RUN NODE_OPTIONS="--max-old-space-size=${NODE_MAX_OLD_SPACE_SIZE}" npm run frontend && npm prune --omit=dev --no-audit --no-fund && npm cache clean --force
RUN mkdir -p /app/data /app/logs /app/uploads /app/skill /app/client/public/images && touch /app/.env

FROM base AS runtime

COPY --from=build --chown=node:node /app /app

ARG BUILD_COMMIT
ARG BUILD_BRANCH
ARG BUILD_DATE
ARG PRODUCT_VERSION

ENV NODE_ENV=production
ENV HOST=0.0.0.0
ENV PORT=3080
ENV BUILD_COMMIT=${BUILD_COMMIT}
ENV BUILD_BRANCH=${BUILD_BRANCH}
ENV BUILD_DATE=${BUILD_DATE}

LABEL org.opencontainers.image.title="BoyanKB"
LABEL org.opencontainers.image.source="https://github.com/yeungmingyik/BoyanKB"
LABEL org.opencontainers.image.version=${PRODUCT_VERSION}
LABEL org.opencontainers.image.revision=${BUILD_COMMIT}
LABEL org.opencontainers.image.created=${BUILD_DATE}

EXPOSE 3080

CMD ["node", "api/server/index.js"]
