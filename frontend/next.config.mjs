/** @type {import('next').NextConfig} */

/**
 * The browser talks to exactly one backend and one auth provider, and both
 * are named in the environment. The content-security policy below is derived
 * from those two values so that a deployment pointing at a new backend does
 * not silently lose its socket to a stale allowlist.
 */
const wsBase = process.env.NEXT_PUBLIC_BACKEND_WS_URL ?? "ws://localhost:8000";
const httpBase = wsBase.replace(/^ws/, "http").replace(/\/$/, "");
const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const dev = process.env.NODE_ENV !== "production";

const connectSrc = [
  "'self'",
  wsBase,
  httpBase,
  supabaseUrl,
  // Supabase Auth's OAuth and OTP flows call the project's own host, and the
  // realtime client — if it ever connects — is on the same host over wss.
  supabaseUrl && supabaseUrl.replace(/^http/, "ws"),
  // next/font fetches nothing at runtime, but the dev overlay does.
  dev && "ws://localhost:*",
  dev && "http://localhost:*",
].filter(Boolean);

const csp = [
  "default-src 'self'",
  // Next.js injects inline bootstrap scripts; without a nonce pipeline
  // `'unsafe-inline'` is required. `'unsafe-eval'` is only for the dev
  // runtime's source-map evaluation and is dropped in production.
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""} blob:`,
  // Tailwind's runtime and framer-motion both write inline style attributes.
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  `connect-src ${connectSrc.join(" ")}`,
  // Live previews come from the sandbox provider over https; agent-generated
  // components and artifacts render from `srcDoc`, which is `'self'`.
  "frame-src 'self' blob: https: http://localhost:*",
  "media-src 'self' blob: https:",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  // Nobody embeds Loom. This is the CSP form of X-Frame-Options: DENY.
  "frame-ancestors 'none'",
  // Only once the backend itself is on TLS. The directive upgrades `ws:` to
  // `wss:` as well as `http:` to `https:`, so a production build pointed at a
  // plain-`ws://` backend (a staging box, a local `next start`) would have its
  // socket silently upgraded into a connection nothing is listening for.
  wsBase.startsWith("wss") && "upgrade-insecure-requests",
]
  .filter(Boolean)
  .join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  // The app uses none of these; saying so stops any embedded content from
  // asking for them either.
  {
    key: "Permissions-Policy",
    value:
      "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
  },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  // Two years, preloadable. Only meaningful over https, which is the only
  // way a production deployment is reached.
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains; preload",
  },
];

const nextConfig = {
  reactStrictMode: true,
  // `X-Powered-By: Next.js` is a fingerprint and nothing else.
  poweredByHeader: false,
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
};

export default nextConfig;
