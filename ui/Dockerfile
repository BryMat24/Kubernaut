# Build context is ui/ itself (not the repo root):
#   docker build -f ui/Dockerfile -t kubernaut-ui ui/
# NEXT_PUBLIC_API_URL is inlined into the client bundle at build time (Next.js
# convention for NEXT_PUBLIC_* vars), so it must be passed as a build arg, not
# just a runtime env var:
#   docker build -f ui/Dockerfile -t kubernaut-ui \
#     --build-arg NEXT_PUBLIC_API_URL=https://api.example.com ui/

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_URL=http://localhost:9000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

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
