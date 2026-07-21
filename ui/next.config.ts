import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Produces .next/standalone (a minimal server + only the deps actually used)
  // so the Docker runner stage doesn't need the full node_modules tree.
  output: "standalone",
};

export default nextConfig;
