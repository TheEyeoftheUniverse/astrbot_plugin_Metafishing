# MetaFishing New User Onboarding Gift Execution Plan

Date: 2026-05-17

## Grade

L: backend registration logic and WebUI presentation are coupled, but the work is still a bounded serial change.

## Plan

1. Add a shared registration reward definition in the user service and issue the requested starter assets during successful new-user creation.
2. Return one canonical onboarding message payload that command registration and Web auto-registration can both reuse.
3. Thread first-registration metadata through the Linux.do Web login path so only newly created accounts trigger the popup.
4. Update the shared Web layout flash bootstrap so onboarding registration success is shown as a modal popup while normal flashes remain toast-based.
5. Run compile and targeted search verification, then record the outcome.

## Verification

- `python3 -m compileall /mnt/c/Users/26459/.astrbot/data/plugins/astrbot_plugin_fishing`
- Targeted `rg` checks for the onboarding copy and first-registration popup markers.

## Cleanup

Record proof under the existing repository docs and avoid leaving temporary runtime artifacts unless verification needs them.
