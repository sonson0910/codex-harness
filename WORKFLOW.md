# Lean workflow

Main owns intent, scope, decisions, integration and final approval. These procedures
do not override host instructions, permissions, repository gates or user authority.

## Risk decides the evidence

- QUICK: no changed behavior, rights, data or operational semantics. Main plus the
  smallest useful check; no mandatory agent or ledger.
- STANDARD: bounded behavior with understood consequences. Main understands,
  implements, verifies and self-reviews. Independent review when uncertainty warrants it.
- ASSURANCE: changed security/trust boundaries, money or durable/destructive data
  semantics, important compatibility or high-consequence uncertainty. After verified
  implementation use independent correctness review, plus separate security review
  for trust/security. Main can still implement small changes directly.

Classify actual semantics: OAuth copy may be QUICK; changing token destinations,
tenant/account binding or authorization can be ASSURANCE in one line.

## Understand, then minimize

Read the affected code, tests, existing patterns and shared callers. Verify decisive
assumptions with the cheapest discriminating check. Reuse code, standard libraries
and installed dependencies. Preserve security, error handling, accessibility and data.
Nontrivial behavior needs a runnable check; never weaken acceptance just to get green.

## Ownership and delegation

Default to Main. Delegate only a bounded, independently verifiable responsibility
with a material benefit over context/integration/rework cost. No file-count threshold
or automatic model escalation. Keep unsettled semantics with Main.

One writer per code region AND mutable runtime resource. Worktrees do not isolate
ports, databases or accounts. Confirm a writer stopped before reassignment. Use
event-driven waiting; timeout is not completion. A practical policy ceiling is two
open descendants across the task, not a claim about the native runtime's slot limit.

## One integrated checkpoint

When independent review is required, review the verified integrated candidate rather
than dispatching a team per helper, test or file. Map overlapping spec/quality/final
obligations to the same checkpoint. Keep important contract decisions and distinct
uncovered gates. Correctness and security may review the same frozen snapshot in parallel.

Main checks findings against source, groups shared root causes, and follows up with
the relevant reviewer using the delta, finding IDs, checks and affected dependencies.
Style wishlists or a quota of findings are not reasons to implement extra work.
Minor repairs do not restart the lifecycle or erase pending obligations.

Keep independent valid review portions; do not copy an old whole-artifact PASS to
new code. Helper, contract, policy, environment or configuration changes can invalidate
unchanged consumers. Revalidate affected interactions. Missing evidence, acceptance-
relevant unknowns and conditional reviews are incomplete. No duplicate final team
when the final artifact's obligations remain satisfied.

## Shared convergence budget

For a new small/medium deliverable needing review: one initial full-review round and
at most two repair/revalidation batch rounds total across all findings/reviewers.
Retain tighter agreed limits. Large rollouts need a declared total checkpoint budget.

Same failure after one fix: stop the writer and recheck the premise. A second fix
needs new causal evidence. Same failure after the second fix or exhausted budget:
block the affected path, preserve evidence and report incomplete. Do not drop bugs.

A→B→C, resume, replacement, reviewer/model/session changes do not reset counters.
Reconcile ongoing task history first; unknown usage is not zero. Additional budget
needs an explicit decision. Command reservations and review rounds are separate.

## Handoff

Bind checks to actual source, scope and environment. Skips and timeouts are not PASS.
Required acceptance is fixed before authoritative execution. Internal readiness is
not approval for risky actions; separate passes do not prove integrated correctness.

Report results, checks and residual uncertainty. Approval is not permission to commit,
push, publish or deploy. Recovery is a narrow reverse-merge of owned changes after
checking current state, never an automatic whole-directory rollback.
