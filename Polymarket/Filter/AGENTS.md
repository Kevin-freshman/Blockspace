# Filter project rules

- Keep this project independent from sibling Polymarket projects.
- Use only public, read-only Polymarket APIs and public, read-only Polygon
  JSON-RPC methods. Polygon RPC is limited to chain identity and transaction
  receipt lookup; do not add trading, wallet-signing, API-key, private-key,
  paid-provider credential, or write-chain functionality.
- Runtime snapshots and caches belong under `data/`; never serve that directory
  as static content or commit it.
- Keep the browser dependency-free and the Python service compatible with
  Python 3.8.
- Normal tests must be offline. Live API checks must be explicit and bounded.
- After a requested Filter change is implemented and its required checks pass,
  automatically stage only `Polymarket/Filter`, create a focused commit, and
  push the current branch to `origin` unless the user explicitly asks not to.
  Never stage unrelated repository changes, never force-push, and stop with a
  clear report if tests fail or the push requires conflict resolution.
