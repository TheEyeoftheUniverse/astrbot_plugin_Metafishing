# MetaFishing New User Onboarding Gift

Date: 2026-05-17

## Goal

Adjust `astrbot_plugin_fishing` so every newly registered player in the MetaFishing project receives a fixed onboarding gift and sees one shared welcome copy across command and Web registration flows.

## Required Reward

Each newly registered user must receive:

- `1000` 金币
- `1` 件 `1` 星鱼竿
- `1` 件 `1` 星饰品
- `100` 个 `1` 星鱼饵

The concrete starter set should map to the first `1`-star templates already present in seed data unless a dedicated starter item is already modeled elsewhere.

## Required Copy

Both command-side and Web-side registration success feedback must display this exact text:

感谢您与鱼光临企业签订雇佣合同，成为光荣的鱼光临雇佣渔夫，期待您今后精彩的捕鱼传说，请收下入职礼物！

The user-visible success message should also clearly expose the gifted contents so the player knows what was issued.

## WebUI Requirement

- Web registration success for a newly created player must surface the onboarding copy in a popup/modal, not only as a passive toast.
- Existing non-registration flashes should keep their current behavior unless needed for this new popup path.

## Acceptance Criteria

- Command `/注册` grants the fixed reward package and returns the shared onboarding copy.
- Linux.do auto-registration in WebUI grants the same package only once for newly created users.
- WebUI first-time registration/login path shows the shared onboarding copy in a modal popup.
- Existing users logging in should not repeatedly receive the registration reward or popup.

## Non-Goals

- No database schema migration for this change.
- No rebalance of shop prices, starter economy, or equipment stats beyond the requested onboarding package.
- No redesign of unrelated login or account pages.
