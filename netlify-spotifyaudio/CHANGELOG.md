# Changelog

## 1.0.22 - 2026-07-17

- Increased brightness of the selected song's thin moving light stroke.
- Kept the same subtle stroke size and border path.

## 1.0.21 - 2026-07-17

- Replaced the active song dot with a thin subtle light stroke.
- Adjusted the animation path so the light reaches the rectangle corners.
- Reduced visibility of the active border effect for a more professional look.

## 1.0.20 - 2026-07-17

- Reduced the active song outside-border light to a smaller dot.
- Smoothed the border route by removing segment resizing at corners.
- Slowed the loop to 15 seconds.

## 1.0.19 - 2026-07-17

- Moved the active song light animation outside/on top of the song row border.
- Removed the rotating inside-rectangle green gradient layer.
- Slowed the small border light to an 11-second outside-edge trace.

## 1.0.18 - 2026-07-17

- Reworked the active song border animation into a slower continuous green gradient layer.
- Removed the corner-by-corner border tracer that could look fast or briefly stuck.

## 1.0.17 - 2026-07-17

- Slowed the active song border tracer for smoother movement.
- Reduced the rainbow light segment size and glow intensity.

## 1.0.16 - 2026-07-17

- Changed the active song animation to a slow rainbow line that travels around the rectangle border.
- Removed the rotating inside-the-box rainbow effect.

## 1.0.15 - 2026-07-17

- Replaced the dim green active-song glow with a rotating rainbow rectangle border.
- Added a bright moving light point that travels around the selected song row.

## 1.0.14 - 2026-07-17

- Added a circular lighting animation inside the currently playing song row.
- Moved the active song highlight automatically when the queue advances to the next or previous track.

## 1.0.13 - 2026-07-17

- Reduced bottom player spacing for a more compact mobile layout.
- Tightened artwork, controls, progress bar, timestamps, and page bottom padding.

## 1.0.12 - 2026-07-17

- Upgraded the Home search bar into AI-style natural language search.
- Added query intent detection for language, latest/trending requests, mood, category, year, and artist/song terms.
- Ranked AI search results against the detected intent instead of only using raw API order.
- Reset cleared searches back to the Trending mix.

## 1.0.11 - 2026-07-17

- Fixed lock-screen Play/Pause controls to use the same real pause/resume behavior as the in-app player.
- Stopped treating normal phone lock/background state as a call interruption.
- Kept interruption recovery for genuine audio pauses caused by calls or OS audio focus changes.

## 1.0.10 - 2026-07-17

- Added call/interruption-aware playback recovery.
- The player now remembers the paused timestamp during app interruptions and resumes from that spot when the app becomes active again.
- Manual pause remains manual and will not auto-resume.

## 1.0.9 - 2026-07-17

- Added a compact animated PulsePlay visualizer to the top-right header area.
- Kept the former Live badge removed while giving the header a more polished active state.

## 1.0.8 - 2026-07-17

- Removed the top-right Live badge from the header.
- Renamed the Results tab to Trending.
- Added a mixed Trending feed for Hollywood, Bollywood, album, dance, party, and viral songs.
- Kept search input behavior while showing search results inside the Trending tab.

## 1.0.7 - 2026-07-17

- Applied latest-relevance sorting to Party, Devotional, and Kids feeds.
- Preserved API/query order as the fallback instead of alphabetical song ordering.
- Fixed Devotional/Kids selected category contrast so white active cards keep readable dark labels.

## 1.0.6 - 2026-07-17

- Changed Home song ordering to prioritize latest, trending, viral, and current-year results.
- Removed alphabetical title sorting from Home language feeds such as Tamil.
- Preserved API/query result order as the fallback so new songs stay near the top.

## 1.0.5 - 2026-07-17

- Integrated the bottom player into the app shell instead of showing it as a separate floating card.
- Matched the bottom player surface, border, and spacing to the rest of the PulsePlay UI.
- Preserved the full mobile control set including previous, rewind, play, fast forward, next, and Video.

## 1.0.4 - 2026-07-17

- Reduced UI font sizes and weights for a more professional app feel.
- Restored all mobile bottom player controls: previous, rewind, play, fast forward, next, and Video.
- Kept the Video button visible on mobile so audio can still switch to video mode.

## 1.0.3 - 2026-07-17

- Rebranded the app from Spotify Audio to PulsePlay with a custom logo.
- Reduced oversized icon typography and removed emoji-heavy tab/button labels.
- Replaced the search emoji with a small CSS-drawn search mark.
- Updated the app manifest and service worker version for the new brand release.

## 1.0.2 - 2026-07-17

- Refreshed the UI with a more modern music app feel.
- Added a richer top bar, rounded navigation pills, softer filters, and polished song rows.
- Improved the mini-player styling with a floating glassy bottom bar.
- Kept the release version tied to the service worker cache for reliable updates.

## 1.0.1 - 2026-07-17

- Updated Home trending searches to favor current, viral, and today-based queries.
- Reduced Home song cache freshness window from 12 hours to 45 minutes.
- Rotated query order so refreshes do not always start from the same search terms.
- Bumped the service worker cache so deployed clients pick up the update.

## Versioning Rules

- Bump `VERSION.json` for every deployed update.
- Keep `sw.js` cache version aligned with the app version.
- Add a changelog entry before deploying and committing.
