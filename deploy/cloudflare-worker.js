/**
 * barelybooting.com apex router
 *
 * Intercepts every request to barelybooting.com and fans out by path:
 *   /api/*       -> barelybooting-server (via Cloudflare Tunnel)
 *   /cerberus/*  -> barelybooting-server (via Cloudflare Tunnel)
 *   everything else -> GitHub Pages (tonyuatkins-afk.github.io)
 *
 * Deployed at the Cloudflare Worker route: barelybooting.com/*
 *
 * Requires a second tunnel public hostname for origin access:
 *   Zero Trust > Networks > Tunnels > (your tunnel) > Public Hostnames >
 *   Add: tunnel.barelybooting.com, service http://barelybooting:8080
 * The Worker fetches through tunnel.barelybooting.com to reach the
 * Flask app; the name is internal-ish and never advertised.
 *
 * Free tier ceiling: 100k requests/day. Well above any plausible
 * traffic for this project.
 */

const TUNNEL_HOST = "tunnel.barelybooting.com";
const PAGES_HOST = "tonyuatkins-afk.github.io";

// Paths that should hit the barelybooting-server origin, not Pages.
// Order matters only if patterns could overlap; these don't.
function routesToTunnel(path) {
  return (
    path.startsWith("/api/") ||
    path.startsWith("/cerberus/")
  );
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const originHost = routesToTunnel(url.pathname)
      ? TUNNEL_HOST
      : PAGES_HOST;

    // Rewrite the hostname; keep path, query, method, headers, body.
    const target = new URL(request.url);
    target.hostname = originHost;
    target.protocol = "https:";
    target.port = "";

    // Host header must match what the origin expects:
    // - GitHub Pages disambiguates user sites via Host.
    // - The tunnel matches public-hostname config via Host.
    const headers = new Headers(request.headers);
    headers.set("Host", originHost);

    // redirect: "manual" so any 30x from the origin passes through to
    // the browser instead of being followed inside the Worker.
    return fetch(target.toString(), {
      method: request.method,
      headers,
      body: request.body,
      redirect: "manual",
    });
  },
};
