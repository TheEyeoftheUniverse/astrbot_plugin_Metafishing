# 2026-05-05 Exchange And Shop Admin Usability Execution Plan

## Grade
L: serial native execution. The work spans exchange services, player templates, admin templates, and image drawing, but the edits are tightly coupled and should stay in one lane.

## Steps
1. Exchange stability
   - Add a daily market snapshot table/repository API.
   - Reuse today's supply/demand snapshot in `get_market_status`.
2. Player WebUI
   - Add futures lot expiry data to `/player/exchange`.
   - Render expiry rows inside commodity cards.
   - Simplify shop OR requirement display.
3. Admin shop UX and correctness
   - Fix edit-modal cost/reward hydration for all supported item kinds.
   - Add per-row type and keyword filtering without external libraries.
   - Normalize rarity labels in shop admin.
   - Normalize rod/accessory/bait modifier percentage display.
4. QQ image style
   - Update `draw/help.py` and `draw/state.py` to the paper/editorial palette used by player WebUI.
5. Verification
   - Run `python3 -m py_compile` for touched Python files.
   - Run JSON/schema checks where applicable.
   - Inspect diffs for accidental unrelated churn.

## Cleanup
No temp runtime artifacts are expected. Leave requirement and plan documents as traceability for this governed run.
