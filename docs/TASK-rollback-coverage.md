# TASK: Investigate and address live rollback-verification coverage

Created 2026-07-05. Source: content-strategy codebase audit (writeup at
`nexplane-canon/content/CLAIMS.md`). Paste the prompt below into a fresh session.
This is a platform-integrity issue — verify rigorously before claiming anything.

---

Investigate and, if confirmed, address Nexplane's live rollback-verification coverage.
This is a platform-integrity issue — verify rigorously before claiming anything.

Context: Nexplane's core value proposition is the rollback guarantee — every
infrastructure change is safely reversible. A codebase audit on 2026-07-05 produced a
concerning finding I need you to independently verify, then fix if real. Per this
project's own principle, "live smoke-verified = done"; declared/code-review-asserted
rollback is a silent liability, not a guarantee.

Finding to verify:
- Only ~12 of ~419 change types (~3%) have a LIVE execute→rollback→verify-prior-state
  smoke phase against real infrastructure — concentrated in backup/restore, catalog
  actions, and the FILO harness.
- Meanwhile 241 change-type JSON defs declare a `rollback_action`, and
  `_IMPLICIT_ROLLBACK_TYPES` in backend/app/services/safety_engine.py has 183 entries.
- The old 2026-06-05 MOAT_ANALYSIS.md claimed "~15 of 200+"; the audit says the numerator
  never grew while the denominator doubled.

Two adjacent findings that also undermine rollback integrity — confirm these too:
1. The AUTOMATIC (verification-failure-triggered) rollback in
   backend/app/workflows/execute_change_workflow.py hardcodes status `rolled_back`
   regardless of whether rollback steps actually succeeded. The four truthful terminal
   states are only computed on the MANUAL path (rollback_executor._determine_rollback_status);
   `manual_recovery_required` is never assigned anywhere.
2. The gating verification (backend/app/services/connector_service.run_verification_checks)
   is still a hardcoded mock returning passed:True without touching infrastructure — so a
   CR can reach a terminal state without real post-state verification, and a failed
   rollback can report clean.

Your task:
1. VERIFY the real numbers first — do not trust the audit blindly. Count change types and
   those with genuine live execute→rollback→verify smoke phases (backend/tests/smoke/*_live.py),
   and report the true current live-rollback-verification ratio with evidence. Confirm or
   correct the ~12/419 figure and both adjacent findings.
2. ASSESS severity: which high-blast-radius change types lack live rollback verification
   and most urgently need it?
3. PLAN a prioritized fix: expand live rollback smoke coverage across change types, and fix
   the auto-rollback truthful-state bug + the mock gating-verifier so completion actually
   means verified.

Constraints (follow the project rules): live infra only, no mocks; smoke runs from the
cloud EC2 runner on Tailscale; changes go through the Nexplane CR lifecycle (dogfooding);
check the AMI cache before provisioning; use the brainstorming skill before designing the fix.

Key files: backend/tests/smoke/ (test_backup_scheduler_live.py, test_catalog_action_live.py,
test_smoke_filo_rollback.py), backend/app/services/safety_engine.py,
backend/app/services/rollback_executor.py, backend/app/workflows/execute_change_workflow.py,
backend/app/services/connector_service.py, backend/app/services/project_rollback_service.py,
docs/product/MOAT_ANALYSIS.md, and the audit writeup at nexplane-canon/content/CLAIMS.md.
