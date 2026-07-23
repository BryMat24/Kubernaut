# Build context is ui/ itself (not the repo root):
#   docker build -f ui/Dockerfile -t kubernaut-ui ui/
# The backend API URL is NOT a build-time value -- app/api/proxy/[...path]/route.ts
# reads a plain API_URL env var at request time in the ui pod's server process, so
# the target is set via the Kubernetes Deployment (see helm/kubernaut/values.yaml),
# not this image.

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN npm run build

# next.config.ts sets output: "standalone", so the runner only needs
# .next/standalone + .next/static + public — not node_modules or source.
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

CMD ["node", "server.js"]
