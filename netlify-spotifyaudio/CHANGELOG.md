# Changelog

## 1.0.1 - 2026-07-17

- Updated Home trending searches to favor current, viral, and today-based queries.
- Reduced Home song cache freshness window from 12 hours to 45 minutes.
- Rotated query order so refreshes do not always start from the same search terms.
- Bumped the service worker cache so deployed clients pick up the update.

## Versioning Rules

- Bump `VERSION.json` for every deployed update.
- Keep `sw.js` cache version aligned with the app version.
- Add a changelog entry before deploying and committing.
