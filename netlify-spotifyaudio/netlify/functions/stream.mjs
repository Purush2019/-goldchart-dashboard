export default async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "access-control-allow-origin": "*",
        "access-control-allow-methods": "GET, OPTIONS",
        "access-control-allow-headers": "range, content-type"
      }
    });
  }

  try {
    const url = new URL(req.url);
    const target = url.searchParams.get("url") || "";

    if (!target || !/^https?:\/\//i.test(target)) {
      return new Response("invalid_url", { status: 400 });
    }

    const reqRange = req.headers.get("range");

    const attemptFetch = async (extraHeaders = {}) => {
      const headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "accept": "audio/mpeg, audio/mp4, audio/*, */*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "referer": "https://www.jiosaavn.com/",
        "origin": "https://www.jiosaavn.com",
        ...extraHeaders
      };
      if (reqRange) headers["range"] = reqRange;
      return fetch(target, { method: "GET", headers });
    };

    let upstream = await attemptFetch();

    // Some CDN nodes reject the origin header — retry without it
    if (!upstream.ok) {
      upstream = await attemptFetch({ origin: undefined });
    }

    if (!upstream.ok || !upstream.body) {
      return new Response(`upstream_failed:${upstream.status}`, { status: 502 });
    }

    const ct = upstream.headers.get("content-type") || "audio/mpeg";
    const cl = upstream.headers.get("content-length");
    const cr = upstream.headers.get("content-range");
    const ar = upstream.headers.get("accept-ranges") || "bytes";

    const resHeaders = new Headers();
    resHeaders.set("content-type", ct);
    resHeaders.set("access-control-allow-origin", "*");
    resHeaders.set("access-control-expose-headers", "content-length,content-range,accept-ranges,content-type");
    resHeaders.set("accept-ranges", ar);
    resHeaders.set("cache-control", "no-store");
    if (cl) resHeaders.set("content-length", cl);
    if (cr) resHeaders.set("content-range", cr);

    return new Response(upstream.body, { status: upstream.status, headers: resHeaders });
  } catch (e) {
    return new Response(`stream_error:${e.message}`, { status: 500 });
  }
};
