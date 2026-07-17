(function () {
  const API_CANDIDATES = [
    "/.netlify/functions/jiosaavn?",
    "https://jiosaavn-api-privatecvc2.vercel.app",
    "https://saavn.dev/api"
  ];
  const audio = document.getElementById("audio");

  const state = {
    trending: [],
    search: [],
    queue: [],
    recent: JSON.parse(localStorage.getItem("sa_recent") || "[]"),
    currentIndex: -1
  };

  const els = {
    trendingGrid: document.getElementById("trendingGrid"),
    searchGrid: document.getElementById("searchGrid"),
    queueGrid: document.getElementById("queueGrid"),
    recentGrid: document.getElementById("recentGrid"),
    queueCount: document.getElementById("queueCount"),
    title: document.getElementById("title"),
    artist: document.getElementById("artist"),
    cover: document.getElementById("cover"),
    playBtn: document.getElementById("playBtn"),
    prevBtn: document.getElementById("prevBtn"),
    nextBtn: document.getElementById("nextBtn"),
    seekBar: document.getElementById("seekBar"),
    currentTime: document.getElementById("currentTime"),
    duration: document.getElementById("duration"),
    searchInput: document.getElementById("searchInput"),
    searchBtn: document.getElementById("searchBtn")
  };

  function fmt(sec) {
    if (!isFinite(sec)) return "0:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
  }

  function isLikelyPreviewUrl(url) {
    if (!url) return true;
    const u = String(url).toLowerCase();
    return u.includes("itunes") || u.includes("preview") || u.includes("30sec");
  }

  function getBestDownloadUrl(downloadUrl) {
    if (!downloadUrl) return "";
    if (Array.isArray(downloadUrl)) {
      const pref = ["320kbps", "160kbps", "128kbps", "96kbps", "48kbps", "12kbps"];
      for (const q of pref) {
        const hit = downloadUrl.find(d => (d.quality || "").toLowerCase() === q);
        const link = hit?.url || hit?.link || "";
        if (link && !isLikelyPreviewUrl(link)) return link;
      }
      const any = downloadUrl
        .map(d => d?.url || d?.link || "")
        .find(link => link && !isLikelyPreviewUrl(link));
      return any || "";
    }
    if (typeof downloadUrl === "string" && !isLikelyPreviewUrl(downloadUrl)) return downloadUrl;
    return "";
  }

  function normalizeSong(item) {
    const id = item.id || item.trackId || item.songId || Math.random().toString(36).slice(2);
    const image =
      item.image?.[2]?.url ||
      item.image?.[1]?.url ||
      item.image?.[0]?.url ||
      item.image ||
      item.artworkUrl100 ||
      "";
    const title = item.name || item.title || item.trackName || "Unknown";
    const artist =
      item.primaryArtists ||
      item.artistName ||
      item.subtitle ||
      (item.artists?.primary || []).map(a => a.name).join(", ") ||
      "-";

    const fullUrl = getBestDownloadUrl(item.downloadUrl || item.download_url);
    return {
      id,
      trackId: id,
      trackName: title,
      artistName: artist,
      artworkUrl100: image,
      previewUrl: fullUrl, // keep old field name for existing player flow
      duration: Number(item.duration || 0)
    };
  }

  async function fetchFromAnyApi(endpoint) {
    for (const base of API_CANDIDATES) {
      try {
        const joiner = base.includes("?") ? "" : "";
        const normalizedEndpoint = endpoint.startsWith("/") ? endpoint.slice(1) : endpoint;
        const url = base.includes("?")
          ? `${base}${normalizedEndpoint.replace("search/songs?", "")}`
          : `${base}${endpoint}`;
        const res = await fetch(url);
        if (!res.ok) continue;
        const json = await res.json();
        return json;
      } catch {}
    }
    return null;
  }

  async function searchSaavn(term, limit = 24) {
    const json = await fetchFromAnyApi(`/search/songs?query=${encodeURIComponent(term)}&page=1&limit=${limit}`);
    const arr = json?.data?.results || json?.results || [];
    return arr.map(normalizeSong).filter(s => s.previewUrl);
  }

  function saveRecent(track) {
    const exists = state.recent.find(t => t.trackId === track.trackId);
    state.recent = [track, ...state.recent.filter(t => t.trackId !== track.trackId)].slice(0, 30);
    localStorage.setItem("sa_recent", JSON.stringify(state.recent));
    if (!exists) renderRecent();
  }

  function card(track, from) {
    const img = (track.artworkUrl100 || "").replace("100x100", "300x300");
    return `
    <div class="card">
      <img src="${img}" alt="">
      <div class="name">${track.trackName || "Unknown"}</div>
      <div class="artist">${track.artistName || "-"}</div>
      <div class="actions">
        <button class="play" onclick="playTrack('${from}', '${track.trackId}')">Play</button>
        <button class="queue" onclick="addToQueue('${from}', '${track.trackId}')">+Queue</button>
      </div>
    </div>`;
  }

  function renderTrending() {
    els.trendingGrid.innerHTML = state.trending.length
      ? state.trending.map(t => card(t, "trending")).join("")
      : `<div class="card"><div class="name">No trending songs found</div></div>`;
  }

  function renderSearch() {
    els.searchGrid.innerHTML = state.search.length
      ? state.search.map(t => card(t, "search")).join("")
      : `<div class="card"><div class="name">No search results</div></div>`;
  }

  function renderRecent() {
    els.recentGrid.innerHTML = state.recent.length
      ? state.recent.map(t => card(t, "recent")).join("")
      : `<div class="card"><div class="name">No recent tracks</div></div>`;
  }

  function renderQueue() {
    els.queueCount.textContent = state.queue.length;
    els.queueGrid.innerHTML = state.queue.length
      ? state.queue.map((t, i) => `
        <div class="row">
          <div>${i + 1}. ${t.trackName} — ${t.artistName}</div>
          <button onclick="playQueueIndex(${i})">Play</button>
        </div>`).join("")
      : `<div class="row">Queue is empty</div>`;
  }

  function byId(source, id) {
    const arr = source === "trending" ? state.trending : source === "search" ? state.search : state.recent;
    return arr.find(t => String(t.trackId) === String(id));
  }

  window.addToQueue = function (source, id) {
    const t = byId(source, id);
    if (!t) return;
    state.queue.push(t);
    renderQueue();
  };

  window.playTrack = function (source, id) {
    const t = byId(source, id);
    if (!t || !t.previewUrl) return alert("No full song URL available for this track");
    let idx = state.queue.findIndex(q => String(q.trackId) === String(t.trackId));
    if (idx < 0) {
      state.queue.push(t);
      idx = state.queue.length - 1;
    }
    state.currentIndex = idx;
    playCurrent();
    renderQueue();
  };

  window.playQueueIndex = function (i) {
    state.currentIndex = i;
    playCurrent();
  };

  function playCurrent() {
    const t = state.queue[state.currentIndex];
    if (!t) return;
    audio.src = t.previewUrl;
    audio.play().catch(() => {});
    els.playBtn.textContent = "⏸";
    els.title.textContent = t.trackName || "Unknown";
    els.artist.textContent = t.artistName || "-";
    els.cover.src = (t.artworkUrl100 || "").replace("100x100", "300x300");
    saveRecent(t);
  }

  function nextTrack() {
    if (!state.queue.length) return;
    state.currentIndex = (state.currentIndex + 1) % state.queue.length;
    playCurrent();
  }

  function prevTrack() {
    if (!state.queue.length) return;
    state.currentIndex = (state.currentIndex - 1 + state.queue.length) % state.queue.length;
    playCurrent();
  }

  async function loadTrending() {
    const year = new Date().getFullYear();
    const month = new Date().toLocaleString("en-US", { month: "long" });
    const terms = [
      `hindi trending today`,
      `tamil trending today`,
      `telugu trending today`,
      `english viral songs today`,
      `bollywood latest songs ${year}`,
      `kollywood ${month} ${year} songs`,
      `tollywood latest songs ${year}`,
      `pop latest songs ${year}`
    ];
    let all = [];
    for (const t of terms) {
      const r = await searchSaavn(t, 10);
      all = all.concat(r);
    }
    const seen = new Set();
    state.trending = all.filter(x => {
      if (!x.trackId || seen.has(x.trackId)) return false;
      seen.add(x.trackId);
      return true;
    }).slice(0, 24);
    renderTrending();
  }

  async function doSearch() {
    const q = els.searchInput.value.trim();
    if (!q) return;
    state.search = await searchSaavn(q, 24);
    renderSearch();
    setView("search");
  }

  function setView(view) {
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    document.querySelectorAll(".nav-btn").forEach(v => v.classList.remove("active"));
    document.getElementById(view + "View")?.classList.add("active");
    document.querySelectorAll(`.nav-btn[data-view="${view}"]`).forEach(btn => btn.classList.add("active"));
  }

  document.querySelectorAll(".nav-btn").forEach(btn => {
    btn.addEventListener("click", () => setView(btn.dataset.view));
  });

  els.searchBtn?.addEventListener("click", doSearch);
  els.searchInput?.addEventListener("keydown", e => { if (e.key === "Enter") doSearch(); });

  els.playBtn?.addEventListener("click", () => {
    if (!audio.src) return;
    if (audio.paused) {
      audio.play().catch(() => {});
      els.playBtn.textContent = "⏸";
    } else {
      audio.pause();
      els.playBtn.textContent = "▶";
    }
  });
  els.nextBtn?.addEventListener("click", nextTrack);
  els.prevBtn?.addEventListener("click", prevTrack);

  audio?.addEventListener("timeupdate", () => {
    if (!audio.duration) return;
    els.seekBar.value = Math.floor((audio.currentTime / audio.duration) * 100);
    els.currentTime.textContent = fmt(audio.currentTime);
    els.duration.textContent = fmt(audio.duration);
  });

  els.seekBar?.addEventListener("input", () => {
    if (!audio.duration) return;
    audio.currentTime = (els.seekBar.value / 100) * audio.duration;
  });

  audio?.addEventListener("ended", nextTrack);

  renderRecent();
  renderQueue();
  loadTrending();

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("./sw.js").catch(() => {}));
  }
})();
