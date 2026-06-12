/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  webpack(config) {
    // onnxruntime-web's `main` is a 25MB Node.js native build. Webpack loads it
    // during worker-bundle resolution and OOMs the SWC jest-worker process.
    // Alias to false (empty module) so the dynamic import in vad.worker.ts
    // throws, the catch block sets useEnergyFallback=true, and VAD still works.
    config.resolve.alias['onnxruntime-web'] = false;
    return config;
  },
};

module.exports = nextConfig;
