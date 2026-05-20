# MetaFishing Sci-Fi Intervention V2 Execution Plan

Date: 2026-05-19

## Grade

XL: this spans schema, repositories, fishing hot-path hooks, three gameplay integrations, item seeding, Bot surfaces, WebUI routes/templates, sync to the test plugin folder, and git delivery.

## Waves

1. Governance and repo shape
   - Freeze requirement and plan docs plus runtime evidence files.
   - Confirm the canonical git worktree and the AstrBot test plugin target.

2. Data and persistence
   - Add migration(s), schema latest updates, Sci-Fi repository/service scaffolding, and the rewrite-chip item seed.
   - Add Sci-Fi state queries, event logging, and leaderboard support.

3. Core gameplay integration
   - Hook fishing for research gain and post-rarity append.
   - Wire branch level-up, apex select/reset, and append-rate calculation.
   - Integrate penalties into Cthulhu, tribulation, and team battle.

4. Interaction surfaces
   - Add Bot handlers and command registrations.
   - Add player WebUI APIs, page routing, template, and navigation entry.

5. Verification and delivery
   - Run compile-focused verification and targeted repository checks.
   - Sync the finished worktree into `/mnt/c/Users/26459/.astrbot/data/plugins/astrbot_plugin_metafishing`.
   - Commit and push the canonical git worktree.

## Verification

- `python3 -m compileall /mnt/c/Users/26459/.astrbot/data/plugins/_work/astrbot_plugin_Metafishing`
- `rg -n "scifi|科技|protocol_rewrite_chip|append_rate|abyss_unity|fate_solitude|resonance_summit" /mnt/c/Users/26459/.astrbot/data/plugins/_work/astrbot_plugin_Metafishing`
- Final `git status --short` and diff review before commit.

## Rollback Rules

- Keep Sci-Fi logic additive and encapsulated behind a dedicated service/repository where practical.
- Avoid broad rewrites of existing fishing, tribulation, Cthulhu, and team battle flows when a narrow hook can preserve current behavior.
- If the cloned git worktree and test plugin folder diverge materially beyond the feature scope, stop before push and reconcile explicitly.

## Cleanup

- Do not leave scratch scripts or temporary files in the repo tree.
- Leave durable requirement, plan, and runtime receipts only.
- Leave the AstrBot test plugin folder runnable with the final implementation.
