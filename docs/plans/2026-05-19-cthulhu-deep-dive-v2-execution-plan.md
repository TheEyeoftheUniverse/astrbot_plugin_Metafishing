# MetaFishing Cthulhu Deep Dive V2 Execution Plan

Date: 2026-05-19

## Grade

XL: this work spans schema, static data, repositories, services, reset orchestration, Bot commands, WebUI APIs/pages, and git delivery. Parallelism is limited by shared write scopes, so execution should stay wave-sequential.

## Waves

1. Governance and shape
   - Freeze requirement and plan documents.
   - Confirm current plugin structure, integration points, and canonical git worktree strategy.

2. Data and persistence
   - Add migration(s), schema latest updates, and static Cthulhu data files.
   - Extend domain models and SQLite repositories for SAN state, true names, votes, authority slots, pollution state, and event logs.

3. Core gameplay
   - Implement Cthulhu service logic for deep-dive trigger, choice staging, reset settlement, true-name generation, calling, authority transfer, pollution activation, rewards, and authority usage.
   - Hook the service into zone entry, sign-in visibility dependencies, and daily reset/startup recovery.

4. Interaction surfaces
   - Add Bot handlers and command registrations.
   - Add player WebUI APIs, templates, and static assets/CSS for Cthulhu state and pollution rendering.

5. Verification and delivery
   - Run compile-focused verification and targeted repository checks.
   - Sync resulting code to the AstrBot test plugin folder if work occurs in a separate git tree.
   - Commit and push from the canonical git worktree.

## Verification

- `python3 -m compileall /mnt/c/Users/26459/.astrbot/data/plugins/astrbot_plugin_metafishing`
- Targeted `rg` checks for Cthulhu commands, APIs, migrations, and data files.
- If a git worktree is created, `git status --short` and a final diff review before commit.

## Rollback Rules

- Keep the new system isolated behind new files/modules and explicit `main.py` wiring where practical.
- Do not rewrite unrelated existing services when an additive hook is sufficient.
- If the canonical repository clone diverges materially from the test folder baseline, stop before pushing and reconcile the delta explicitly.

## Cleanup

- Do not leave temporary scripts or scratch files in the plugin tree.
- Leave the test plugin folder runnable with the final implementation.
- Record only durable requirement and plan artifacts under `docs/`.
