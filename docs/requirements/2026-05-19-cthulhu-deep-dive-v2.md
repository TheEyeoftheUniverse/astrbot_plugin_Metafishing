# MetaFishing Cthulhu Deep Dive V2

Date: 2026-05-19

## Goal

Implement the Cthulhu Deep Dive V2 gameplay described in `克苏鲁深潜玩法V2策划案.md` as a production-ready system inside `astrbot_plugin_metafishing`, covering data model, daily settlement, true-name calling, authority gameplay, WebUI, and player command access.

## Scope

- Add the Cthulhu gameplay loop: daily deep dive ticket consumption in zone `7`, pending event choice, reset-time settlement, true-name generation, public calling, authority transfer, SAN economy, and pollution visibility.
- Add the static data required by the design: event library, true-name pools, authority slot definitions, and pollution definitions.
- Add database support for the new system through migrations and latest schema alignment.
- Expose player-facing access through Bot commands and player WebUI APIs/pages.
- Integrate the system with existing user, inventory, fishing zone, sign-in, and daily reset flows without rewriting unrelated gameplay.

## Functional Requirements

- Entering zone `7` with a `deepdive_ticket` must consume at most one ticket per daily marker and create one pending deep-dive event.
- Pending events must allow player choice staging and must resolve only at daily reset. Unchosen events must be auto-resolved with a random choice.
- Daily reset must settle all pending deep dives, apply great-failure SAN penalties and forced pollution, then recover SAN by `5`, then clear expired forced-pollution state and daily flags.
- Settled deep dives must grant one true name with bound `god_type`, `tier`, threshold, and owner, while enforcing a max inventory size of `10`.
- Players must be able to list true names, start public calling, vote by exact name string with SAN cost, and inspect active calling progress.
- Calling completion must transfer the matching authority slot, compensate replaced holders with `cthulhu_san_cap_token`, apply global SAN loss, activate one permanent pollution when available, distribute sign-in rewards, and consume the true name.
- Authority usage must support the four god types in the design with their fixed SAN costs and tier-scaled effects.
- The system must expose the player SAN state, pending event, owned authorities, global authority board, event log, and visible pollution state in WebUI and Bot surfaces.
- Pollution must only render in WebUI. Textual and CSS pollution must follow SAN-threshold or forced-pollution visibility rules.

## Non-Goals

- No LLM-generated events or dynamic Cthulhu copy.
- No Bot-side pollution rendering in V1.
- No redesign of unrelated market, gacha, aquarium, or team-battle systems.
- No new external service dependency beyond the existing plugin stack.

## Constraints

- Reuse the plugin's current SQLite repository pattern and Quart-based player WebUI.
- Keep the current `daily_reset_hour` as the single timing authority.
- Preserve existing gameplay behavior outside the explicit integration points required for Cthulhu.
- Final code must remain available in the AstrBot test plugin folder and also be committed to the canonical online repository.

## Acceptance Criteria

- A registered player with a `deepdive_ticket` can enter zone `7`, receive one pending event, stage a choice, and receive a true name only after reset settlement.
- Calling a true name and voting it to threshold transfers or refreshes the matching authority slot and distributes all configured side effects.
- `/深潜状态`, `/真名列表`, `/发起呼唤`, `/呼唤`, `/权柄`, `/权柄使用`, `/全服权柄`, and `/呼唤进度` are available and wired to the new system.
- Player WebUI exposes Cthulhu state, active calls, authority board, pollution visibility API, and authority actions.
- At least one verification pass succeeds for syntax/compile health, and the resulting implementation is committed and pushed from a git worktree.
