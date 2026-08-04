# Filter project rules

- Keep this project independent from sibling Polymarket projects.
- Use only public, read-only Polymarket APIs. Do not add trading, wallet-signing,
  API-key, private-key, or RPC functionality.
- Runtime snapshots and caches belong under `data/`; never serve that directory
  as static content or commit it.
- Keep the browser dependency-free and the Python service compatible with
  Python 3.8.
- Normal tests must be offline. Live API checks must be explicit and bounded.
- Do not run `git add`, `git commit`, or `git push` unless the user asks.
