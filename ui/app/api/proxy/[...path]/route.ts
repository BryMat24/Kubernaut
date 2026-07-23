import type { NextRequest } from "next/server";

// Read at request time in the ui pod's Node.js server process -- unlike
// NEXT_PUBLIC_* vars, this is never inlined into the client bundle, so the
// Kubernetes Deployment's API_URL env var actually takes effect here.
const API_URL = process.env.API_URL ?? "http://localhost:9000";

async function proxy(request: NextRequest, path: string[]): Promise<Response> {
  const target = `${API_URL}/${path.join("/")}${request.nextUrl.search}`;

  const response = await fetch(target, {
    method: request.method,
    headers: { "Content-Type": "application/json" },
    body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.text(),
  });

  return new Response(response.body, {
    status: response.status,
    headers: response.headers,
  });
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<Response> {
  const { path } = await params;
  return proxy(request, path);
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
): Promise<Response> {
  const { path } = await params;
  return proxy(request, path);
}
