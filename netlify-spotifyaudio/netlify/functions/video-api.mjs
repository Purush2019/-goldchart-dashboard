const YT_BASE = "https://www.youtube.com/youtubei/v1";
const YT_SEARCH = YT_BASE + "/search?prettyPrint=false";
const YT_BROWSE = YT_BASE + "/browse?prettyPrint=false";
const YT_NEXT = YT_BASE + "/next?prettyPrint=false";
const YT_HEADERS = {
  "Content-Type": "application/json",
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  "Accept-Language": "en-US,en;q=0.9",
  "Origin": "https://www.youtube.com",
  "Referer": "https://www.youtube.com/",
};
const YT_CTX = { client: { clientName: "WEB", clientVersion: "2.20260320.00.00", hl: "en", gl: "IN", originalUrl: "https://www.youtube.com/", platform: "DESKTOP" } };

function parseVideoItems(items) {
  const videos = [];
  for (const item of items) {
    // Handle different renderer types
    const vr = item.videoRenderer || item.compactVideoRenderer || item.gridVideoRenderer || {};
    const vid = vr.videoId;
    if (!vid) {
      // Try richItemRenderer (used in browse/home responses)
      const ri = item.richItemRenderer?.content?.videoRenderer;
      if (ri?.videoId) {
        const titleRuns = (ri.title || {}).runs || [];
        const title = titleRuns[0]?.text || "";
        const thumbs = (ri.thumbnail || {}).thumbnails || [];
        const thumb = thumbs.length ? thumbs[thumbs.length - 1].url : "";
        const ownerRuns = (ri.ownerText || {}).runs || [];
        const channel = ownerRuns[0]?.text || "";
        const views = (ri.viewCountText || {}).simpleText || (ri.viewCountText?.runs || []).map(r => r.text).join("") || "";
        const duration = (ri.lengthText || {}).simpleText || (ri.lengthText?.accessibility?.accessibilityData?.label) || "";
        const published = (ri.publishedTimeText || {}).simpleText || "";
        const chThumbs = ((ri.channelThumbnailSupportedRenderers || {}).channelThumbnailWithLinkRenderer || {}).thumbnail?.thumbnails || [];
        const chAvatar = chThumbs[0]?.url || "";
        videos.push({ id: ri.videoId, title, thumbnail: thumb, channel, channelAvatar: chAvatar, views, duration, published });
      }
      continue;
    }
    const titleRuns = (vr.title || {}).runs || [];
    const title = titleRuns[0]?.text || "";
    const thumbs = (vr.thumbnail || {}).thumbnails || [];
    const thumb = thumbs.length ? thumbs[thumbs.length - 1].url : "";
    const ownerRuns = (vr.ownerText || {}).runs || [];
    const channel = ownerRuns[0]?.text || "";
    const views = (vr.viewCountText || {}).simpleText || (vr.viewCountText?.runs || []).map(r => r.text).join("") || "";
    const duration = (vr.lengthText || {}).simpleText || "";
    const published = (vr.publishedTimeText || {}).simpleText || "";
    const chThumbs = ((vr.channelThumbnailSupportedRenderers || {}).channelThumbnailWithLinkRenderer || {}).thumbnail?.thumbnails || [];
    const chAvatar = chThumbs[0]?.url || "";
    videos.push({ id: vid, title, thumbnail: thumb, channel, channelAvatar: chAvatar, views, duration, published });
  }
  return videos;
}

function extractVideosPaged(data) {
  const contents = data?.contents?.twoColumnSearchResultsRenderer?.primaryContents?.sectionListRenderer?.contents || [];
  const allItems = [];
  let token = null;
  for (const sec of contents) {
    const items = sec?.itemSectionRenderer?.contents || [];
    allItems.push(...items);
    const t = sec?.continuationItemRenderer?.continuationEndpoint?.continuationCommand?.token;
    if (t) token = t;
  }
  return { videos: parseVideoItems(allItems), token };
}

function extractContinuation(data) {
  const allItems = [];
  let token = null;
  for (const cmd of (data.onResponseReceivedCommands || data.onResponseReceivedActions || [])) {
    const ci = cmd?.appendContinuationItemsAction?.continuationItems ||
               cmd?.reloadContinuationItemsCommand?.continuationItems || [];
    for (const sec of ci) {
      const items = sec?.itemSectionRenderer?.contents || [];
      allItems.push(...items);
      // richItemRenderer in continuation
      if (sec.richItemRenderer) allItems.push(sec);
      const t = sec?.continuationItemRenderer?.continuationEndpoint?.continuationCommand?.token;
      if (t) token = t;
    }
  }
  return { videos: parseVideoItems(allItems), token };
}

// Extract videos from YouTube browse API response (home/trending)
function extractBrowse(data) {
  const allItems = [];
  let token = null;
  // Trending page structure
  const tabs = data?.contents?.twoColumnBrowseResultsRenderer?.tabs || [];
  for (const tab of tabs) {
    const sections = tab?.tabRenderer?.content?.sectionListRenderer?.contents || [];
    for (const sec of sections) {
      const shelf = sec?.itemSectionRenderer?.contents || [];
      for (const s of shelf) {
        const items = s?.shelfRenderer?.content?.expandedShelfContentsRenderer?.items || [];
        allItems.push(...items);
        // Also handle direct video renderers
        if (s.videoRenderer) allItems.push(s);
        if (s.gridRenderer) {
          allItems.push(...(s.gridRenderer.items || []));
        }
      }
      // Continuation
      const ct = sec?.continuationItemRenderer?.continuationEndpoint?.continuationCommand?.token;
      if (ct) token = ct;
    }
  }
  // Rich grid (home page structure)
  const richGrid = data?.contents?.twoColumnBrowseResultsRenderer?.tabs?.[0]?.tabRenderer?.content?.richGridRenderer;
  if (richGrid) {
    for (const item of (richGrid.contents || [])) {
      if (item.richItemRenderer) allItems.push(item);
      if (item.richSectionRenderer) {
        const subItems = item.richSectionRenderer?.content?.richShelfRenderer?.contents || [];
        allItems.push(...subItems);
      }
      const ct = item?.continuationItemRenderer?.continuationEndpoint?.continuationCommand?.token;
      if (ct) token = ct;
    }
  }
  return { videos: parseVideoItems(allItems), token };
}

async function ytSearch(body) {
  const resp = await fetch(YT_SEARCH, { method: "POST", headers: YT_HEADERS, body: JSON.stringify(body) });
  return resp.json();
}

async function ytBrowse(body) {
  const resp = await fetch(YT_BROWSE, { method: "POST", headers: YT_HEADERS, body: JSON.stringify(body) });
  return resp.json();
}

function durSecs(d) {
  if (!d) return 0;
  const parts = d.split(":");
  try {
    if (parts.length === 3) return parseInt(parts[0]) * 3600 + parseInt(parts[1]) * 60 + parseInt(parts[2]);
    if (parts.length === 2) return parseInt(parts[0]) * 60 + parseInt(parts[1]);
  } catch { return 0; }
  return 0;
}

function ok(data) {
  return new Response(JSON.stringify(data), {
    status: 200,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
  });
}

function err(msg, code = 400) {
  return new Response(JSON.stringify({ error: msg }), {
    status: code,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
  });
}

export default async (req) => {
  const url = new URL(req.url);
  const route = url.pathname.replace(/^\/api\/video\//, "").replace(/\/$/, "");
  const qs = Object.fromEntries(url.searchParams);

  // Handle continuation (shared logic — works for both search and browse)
  async function handleContinuation(token) {
    // Try browse continuation first, then search
    let data = await ytBrowse({ context: YT_CTX, continuation: token });
    let result = extractContinuation(data);
    if (!result.videos.length) {
      data = await ytSearch({ context: YT_CTX, continuation: token });
      result = extractContinuation(data);
    }
    return ok(result);
  }

  try {
    // ── SEARCH: Pass query directly to YouTube search (no forced params = same results as YouTube) ──
    if (route === "search") {
      const q = qs.q || "";
      const token = qs.token || "";
      const sp = qs.sp || "";
      if (!q && !token) return err("missing q");
      if (token) return handleContinuation(token);
      // Only pass sp if user explicitly set filters; otherwise let YouTube rank naturally
      const body = { context: YT_CTX, query: q };
      if (sp) body.params = sp;
      const data = await ytSearch(body);
      return ok(extractVideosPaged(data));
    }

    // ── HOME: Use YouTube's actual browse API for personalized-style home feed ──
    if (route === "home") {
      const token = qs.token || "";
      if (token) return handleContinuation(token);
      // Browse the YouTube home page (FEwhat_to_watch)
      const data = await ytBrowse({ context: YT_CTX, browseId: "FEwhat_to_watch" });
      const result = extractBrowse(data);
      // If browse returned enough videos, use them
      if (result.videos.length >= 5) return ok(result);
      // Fallback: search for trending content
      const fallback = await ytSearch({ context: YT_CTX, query: "trending today" });
      return ok(extractVideosPaged(fallback));
    }

    // ── TRENDING: Use YouTube's actual trending page ──
    if (route === "trending") {
      const token = qs.token || "";
      if (token) return handleContinuation(token);
      const cat = qs.cat || "";
      // YouTube trending browse params for different categories
      const trendingParams = {
        "": "",            // Default trending
        music: "4gINGgt6MgwSAmdt",     // Trending Music
        gaming: "4gINGgt2MgwSAmdt",    // Trending Gaming  
        news: "",          // No dedicated browse param for news
        movies: "4gINGgt0MgwSAmdt",    // Trending Movies
      };
      // Use browse API for trending
      const body = { context: YT_CTX, browseId: "FEtrending" };
      const bp = trendingParams[cat];
      if (bp) body.params = bp;
      const data = await ytBrowse(body);
      const result = extractBrowse(data);
      if (result.videos.length >= 3) return ok(result);
      // Fallback for categories without browse support
      const yr = new Date().getFullYear();
      const fallbackQueries = {
        "": "trending India today",
        music: "trending music India today",
        gaming: "trending gaming India",
        news: "India news today",
        sports: `cricket highlights ${yr}`,
        comedy: "trending comedy India",
        tech: "tech reviews India",
      };
      const fq = fallbackQueries[cat] || fallbackQueries[""];
      const fdata = await ytSearch({ context: YT_CTX, query: fq });
      return ok(extractVideosPaged(fdata));
    }

    if (route === "movies") {
      const token = qs.token || "";
      if (token) return handleContinuation(token);
      const langMap = { "": "", tamil: "Tamil", hindi: "Hindi", telugu: "Telugu", malayalam: "Malayalam", kannada: "Kannada", english: "English", korean: "Korean", bengali: "Bengali" };
      const genreMap = { "": "", action: "action", comedy: "comedy", romance: "romantic", thriller: "thriller", horror: "horror", drama: "drama", dubbed: "dubbed", family: "family" };
      const l = langMap[qs.lang || ""] || "";
      const g = genreMap[qs.genre || ""] || "";
      const parts = [];
      if (qs.artist) parts.push(qs.artist);
      if (l) parts.push(l);
      if (g) parts.push(g);
      if (qs.year) parts.push(qs.year);
      else parts.push(new Date().getFullYear().toString());
      parts.push("full movie");
      const data = await ytSearch({ context: YT_CTX, query: parts.join(" ") });
      const { videos, token: cont } = extractVideosPaged(data);
      let movies = videos.filter(v => durSecs(v.duration) >= 3600);
      if (!movies.length) movies = videos.filter(v => durSecs(v.duration) >= 1200);
      return ok({ videos: movies, token: cont });
    }

    if (route === "songs") {
      const token = qs.token || "";
      if (token) return handleContinuation(token);
      const langMap = { "": "", tamil: "Tamil", hindi: "Hindi", telugu: "Telugu", malayalam: "Malayalam", kannada: "Kannada", english: "English", korean: "Korean", bengali: "Bengali", marathi: "Marathi", punjabi: "Punjabi", gujarati: "Gujarati" };
      const l = langMap[qs.lang || ""] || "";
      const parts = [];
      if (qs.singer) parts.push(qs.singer);
      if (l) parts.push(l);
      if (qs.cat) parts.push(qs.cat);
      else parts.push("songs");
      if (qs.year) parts.push(qs.year);
      else parts.push(new Date().getFullYear().toString());
      parts.push("video song");
      const data = await ytSearch({ context: YT_CTX, query: parts.join(" "), params: "EgIQAQ%3D%3D" });
      return ok(extractVideosPaged(data));
    }

    if (route === "suggest") {
      const q = qs.q || "";
      if (!q) return ok([]);
      const sugUrl = `https://suggestqueries.google.com/complete/search?client=youtube&ds=yt&q=${encodeURIComponent(q)}`;
      const resp = await fetch(sugUrl, { headers: { "User-Agent": "Mozilla/5.0" } });
      const raw = await resp.text();
      const start = raw.indexOf("(");
      if (start > 0) {
        const inner = raw.slice(start + 1, -1);
        const parsed = JSON.parse(inner);
        const suggestions = parsed[1] ? parsed[1].map(s => s[0]) : [];
        return ok(suggestions);
      }
      return ok([]);
    }

    return err("unknown route", 404);
  } catch (e) {
    return err("server error: " + e.message, 500);
  }
};

export const config = { path: "/api/video/*" };
