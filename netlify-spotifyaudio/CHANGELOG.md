# Changelog

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
