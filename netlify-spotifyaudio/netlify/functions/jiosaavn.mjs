const JIOSAAVN_API = "https://www.jiosaavn.com/api.php";
const jsonHeaders = { "user-agent": "Mozilla/5.0", "referer": "https://www.jiosaavn.com/" };

async function jioCall(params) {
  const url = `${JIOSAAVN_API}?${new URLSearchParams({
    ...params,
    _format: "json",
    _marker: "0",
    api_version: "4",
    ctx: "web6dot0"
  })}`;
  const r = await fetch(url, { signal: AbortSignal.timeout(8000), headers: jsonHeaders });
  if (!r.ok) return null;
  return r.json();
}

function artistNames(song, rolePattern) {
  const groups = song?.more_info?.artistMap || {};
  const values = Object.values(groups).flat().filter(Boolean);
  return values
    .filter(a => !rolePattern || rolePattern.test(String(a.role || "")))
    .map(a => a.name)
    .filter(Boolean)
    .join(", ");
}

async function normalizeJioSong(song) {
  const encrypted = song?.more_info?.encrypted_media_url || "";
  let mediaUrl = song?.media_url || song?.more_info?.vlink || "";
  if (encrypted) {
    try {
      const auth = await jioCall({ __call: "song.generateAuthToken", url: encrypted, bitrate: "320" });
      if (auth?.auth_url) mediaUrl = auth.auth_url;
    } catch {}
  }
  const primaryArtists = artistNames(song, /primary/i) || song.subtitle || "";
  const singers = artistNames(song, /singer|primary/i) || primaryArtists;
  return {
    id: song.id,
    song: song.title,
    title: song.title,
    name: song.title,
    album: song.more_info?.album || "",
    year: song.year || song.more_info?.release_date?.slice?.(0, 4) || "",
    release_date: song.more_info?.release_date || "",
    primary_artists: primaryArtists,
    singers,
    starring: artistNames(song, /starring/i),
    image: String(song.image || "").replace(/150x150|50x50/g, "500x500"),
    duration: song.more_info?.duration || song.duration || 0,
    media_url: mediaUrl,
    perma_url: song.perma_url || "",
    source: "jiosaavn"
  };
}

async function directJioSaavnSearch(query) {
  const autocomplete = await jioCall({ __call: "autocomplete.get", query });
  const songs = [...(autocomplete?.songs?.data || [])];
  const entityFetches = [];
  for (const playlist of (autocomplete?.playlists?.data || []).slice(0, 3)) {
    if (playlist.id) entityFetches.push(jioCall({ __call: "playlist.getDetails", listid: playlist.id }));
  }
  for (const album of (autocomplete?.albums?.data || []).slice(0, 3)) {
    if (album.id) entityFetches.push(jioCall({ __call: "content.getAlbumDetails", albumid: album.id }));
  }
  const details = await Promise.all(entityFetches.map(p => p.catch(() => null)));
  details.forEach(data => {
    if (Array.isArray(data?.list)) songs.push(...data.list);
  });
  const seen = new Set();
  const unique = songs.filter(song => {
    const id = song?.id;
    if (!id || seen.has(id)) return false;
    seen.add(id);
    return song.type === "song" || song.more_info?.encrypted_media_url || song.more_info?.vlink;
  }).slice(0, 40);
  const normalized = await Promise.all(unique.map(normalizeJioSong));
  return normalized.filter(song => song.id && song.media_url);
}

export default async (req) => {
  const cors = { "content-type": "application/json", "access-control-allow-origin": "*" };
  try {
    const url = new URL(req.url);
    const query = url.searchParams.get("query") || "";
    if (!query.trim()) return new Response("[]", { status: 200, headers: cors });

    const direct = await directJioSaavnSearch(query);
    if (direct.length) return new Response(JSON.stringify(direct), { status: 200, headers: cors });

    return new Response("[]", { status: 200, headers: cors });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e.message) }), { status: 200, headers: cors });
  }
};
