# 2026-05-05 Exchange And Shop Admin Usability

## Goal
Stabilize exchange market descriptors, expose futures expiry information, and make shop configuration practical and safe in the admin UI.

## Deliverables
- Exchange supply/demand is refreshed once per date and no longer changes immediately after a player buys or sells.
- Player WebUI futures cards show each holding lot's expiry time and remaining time.
- Player shop cards summarize OR requirements without adding large "或" separators.
- Admin shop item editing preserves existing costs and rewards, including rod/accessory/bait costs.
- Admin item pickers provide type and keyword filtering for fish, rods, accessories, items, and baits.
- Admin rarity text uses `1☆` through `5☆`, then `6★+`.
- Rod/accessory/bait modifier displays consistently format chance values as percentages and multiplier values as percentage deltas where appropriate.
- QQ `状态` and `钓鱼帮助` image output uses the player WebUI paper/editorial visual language.

## Constraints
- Keep existing data model semantics for shop cost groups and AND/OR relations.
- Do not add third-party frontend dependencies.
- Preserve existing uncommitted fixes in this worktree.

## Acceptance Checks
- Python compile check passes for touched Python files.
- Jinja template syntax compiles for touched templates where local dependencies allow it.
- Admin edit modal can reopen an item with rod/accessory/item/fish/bait costs without blanking them.
- Empty cost rows remain ignored, but valid rows are never dropped due to missing frontend mappings.
