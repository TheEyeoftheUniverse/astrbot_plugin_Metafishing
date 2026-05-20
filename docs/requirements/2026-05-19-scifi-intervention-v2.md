# MetaFishing Sci-Fi Intervention V2

Date: 2026-05-19

## Goal

Implement the gameplay described in `科幻干预玩法V2策划案.md` inside `astrbot_plugin_metafishing` as a production-ready system with backend state, fishing integration, cross-system penalties, Bot commands, and player WebUI access.

## Scope

- Add persistent Sci-Fi state for research points, three branch levels, apex protocol selection, append counters, and event logging.
- Add Sci-Fi research gain in zone `5`, plus the post-rarity `6+` append mechanic that can replace non-`6+` fish with an eligible `6+` result from the same zone.
- Add branch level-up, append-rate calculation, apex selection, and apex reset through the `protocol_rewrite_chip` item.
- Apply Sci-Fi penalties to the three linked systems:
  - Cthulhu deep-dive great-failure pressure and `abyss_unity` forced pollution.
  - Tribulation self success-rate penalty, guard invalidation, and `fate_solitude` extra multiplier.
  - Team battle hit-roll penalty and `resonance_summit` extra lockout.
- Expose player-facing access through Bot commands, player WebUI APIs, and a dedicated Sci-Fi page integrated into the existing navigation.
- Deliver the result in the canonical git worktree, sync the runnable AstrBot test plugin folder, and push upstream.

## Functional Requirements

- First access must auto-create a Sci-Fi profile with zero points, zero branch levels, no apex protocol, and zero append stats.
- Only successful fishing in zone `5` may grant research points, and appended `6+` fish must still award points by original rarity.
- Branch level-up must enforce per-level costs `30/60/100/150/200`, max level `5`, no downgrade path, and insufficient-point rejection.
- Total append rate must be calculated in basis points from the three branch cumulative values plus apex bonuses, capped below `10000`.
- When a non-`6+` fish is drawn, Sci-Fi append must roll once against the player's append rate and, on success, replace the result with a weighted `6+` fish from the same zone when available.
- Apex selection must enforce unlock conditions and one-active-apex semantics.
- Apex reset must require one `protocol_rewrite_chip`, clear only the apex state, and preserve research points plus branch levels.
- Cthulhu, tribulation, and team battle outcomes must reflect the configured Sci-Fi penalties for the acting player.
- Player WebUI must expose state, append-rate breakdown, level-up, apex select/reset, leaderboard, and recent event log access.
- Bot access must expose status, level-up, apex select/reset, append-rate query, and leaderboard commands.

## Non-Goals

- No LLM-generated Sci-Fi text.
- No daily reset for Sci-Fi points, levels, or apex state.
- No new external service dependency.
- No rewrite of unrelated market, gacha, aquarium, or expedition systems.

## Constraints

- Reuse the plugin's SQLite migration pattern, repository style, service wiring, Bot command pattern, and Quart player WebUI stack.
- Keep behavior additive where possible instead of rewriting stable gameplay flows.
- Keep the AstrBot test plugin folder runnable after sync.
- Final implementation must be committed and pushed from a real git worktree.

## Acceptance Criteria

- A player fishing in zone `5` gains research points and can spend them to level Sci-Fi branches.
- A player with nonzero append rate can occasionally convert non-`6+` catches into valid zone `6+` fish, while research gain still uses original rarity.
- `/科技`, `/加点`, `/觉醒`, `/重写协议`, `/追加率`, and `/科技榜` are available and correctly wired.
- The player WebUI exposes a Sci-Fi page and APIs for state, append rate, level-up, apex operations, leaderboard, and event log.
- Cthulhu, tribulation, and team battle behavior changes when the acting user carries the relevant Sci-Fi penalties.
- Verification passes for syntax/compile health, and the final changes are synced to the AstrBot test plugin folder, committed, and pushed.
