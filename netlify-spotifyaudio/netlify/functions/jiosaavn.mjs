export default async (req) => {
  const cors = { "content-type": "application/json", "access-control-allow-origin": "*" };
  try {
    const url = new URL(req.url);
    const query = url.searchParams.get("query") || "";
    if (!query.trim()) return new Response("[]", { status: 200, headers: cors });

    const upstreams = [
      `https://saavnapi-nine.vercel.app/result/?query=${encodeURIComponent(query)}&lyrics=false&songdata=true`,
      `https://saavn.me/search/songs?query=${encodeURIComponent(query)}&limit=50`
    ];

    for (const endpoint of upstreams) {
      try {
        const r = await fetch(endpoint, { signal: AbortSignal.timeout(8000), headers: { "user-agent": "Mozilla/5.0" } });
        if (!r.ok) continue;
        const data = await r.json();
        // saavnapi-nine returns an array; saavn.me returns { data: { results: [...] } }
        const list = Array.isArray(data) ? data : (data?.data?.results || data?.results || []);
        if (!list.length) continue;
        return new Response(JSON.stringify(list), { status: 200, headers: cors });
      } catch {}
    }

    return new Response("[]", { status: 200, headers: cors });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e.message) }), { status: 200, headers: cors });
  }
};
