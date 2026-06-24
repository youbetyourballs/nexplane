# Nexplane Website Revision Notes
# Current State + Proposed Direction

Created: 2026-06-16

---

## Current Site Assessment

The current site (nexplane.ai, static HTML/CSS/JS, hosted on Netlify) is
well-structured and technically accurate. The rollback guarantee is prominent,
the AI safety section is relevant, and the early access form is targeted.

**What's working:**
- "Every change is undoable" is a strong, concrete promise
- The comparison table (vs. Terraform, Ansible, SOAR, AWS SSM) is useful
- The AI safety section is timely and well-positioned
- 80+ change types and the 6 capability cards show depth
- Early access form captures the right signals (role, fleet size, pain point)

**What's missing or weaker:**
- The *why this category exists* story — the incentive misalignment between
  security and infra teams isn't on the page. A visitor doesn't understand
  the structural problem before being offered the solution.
- No founder voice or credibility. 4x CISO, NYU professor, Drawbridge Networks —
  none of this is on the site. It reads like a product page, not a conviction.
- The OS abstraction framing isn't there. "Security control plane" is accurate
  but doesn't explain the category the way the OS analogy does.
- "Private beta" and "early access" framing undersells the maturity. The live
  smoke test suite, 71 connectors, and rollback verification are more credible
  than the language implies.
- No design partner ask. The current CTA is "get early access" — passive. A
  design partner ask is active and collaborative.
- The live infrastructure testing rigor — the competitive differentiator —
  is not mentioned anywhere on the site.

---

## Proposed Revised Structure

### Hero Section — Reframe the Category

**Current headline:** "The security control plane where every change is undoable."

**Proposed direction:**
Lead with the problem statement before the solution. The current headline
assumes the visitor already understands why they need this. Most don't.

Option A (OS framing):
> "The operating system for infrastructure change."
> Subhead: "Cloud-agnostic primitives for every security and infra operation.
> Safe, reversible, auditable — regardless of which tools you're running."

Option B (execution gap framing):
> "Security teams know what needs to happen. Nexplane makes it safe to do it."
> Subhead: "A governed execution layer for every infrastructure change —
> with rollback built in, approval gates that fit your workflow, and an
> audit trail that proves it worked."

Option C (keep rollback but add the why):
> "The teams that need to change your infrastructure are incentivized not to.
> Nexplane fixes that."
> Subhead: "A safe, governed execution layer that gives security teams
> direct execution capability — and gives infrastructure teams the rollback
> guarantee they need to say yes."

**Recommendation:** Option B or a hybrid. The OS framing is powerful in
conversation but may be too abstract as a hero headline for a first visit.
The execution gap framing is immediately relatable to the ICP. Test both.

---

### Problem Section — Add the Structural Story

Currently missing entirely. Add a section before the product features that
names the structural problem:

> **Security knows what needs to happen. Execution is the problem.**
>
> Security teams have a clear picture of the infrastructure changes required
> to reduce risk. The problem has never been what to do. It's been who can
> safely do it.
>
> Infrastructure teams are measured on uptime. They're punished for outages.
> They're not measured on security posture. So security requests sit in
> backlogs — not because anyone is negligent, but because the incentives
> don't align.
>
> Nexplane is the execution layer that resolves this. Security gets a
> governed path to execute changes directly. Infrastructure gets the rollback
> guarantee that makes delegation safe.

This one section does more positioning work than the rest of the page
combined. It tells the visitor what world they're in before offering a
solution.

---

### OS / Abstraction Section — Add the Category Framing

Add a section that explains what Nexplane actually is at a conceptual level.
This earns technical credibility with the SRE/sysadmin audience.

> **Cloud-agnostic primitives, like an operating system for change.**
>
> An operating system abstracts away hardware — you write to the OS API,
> not the disk controller. It enforces safety boundaries and makes the
> same code run on any hardware.
>
> Nexplane does the same thing one layer up. Whether you're on AWS or GCP,
> Active Directory or Okta, HashiCorp Vault or AWS Secrets Manager — you
> define a change request, not a connector-specific script. The platform
> resolves it to the right executor, enforces rollback, and verifies the
> result.
>
> The abstraction is the safety guarantee. The same change request that
> rotates a key in Vault today will rotate it in whatever replaces Vault
> tomorrow.

---

### Rollback Section — Add the Proof

The current rollback section is good. Add one thing: the live infrastructure
verification angle.

> **We test every rollback against live infrastructure before it ships.**
>
> Most platforms claim rollback capability. We verify it. Every change type
> ships with a live smoke test that executes the change, verifies the result,
> triggers the rollback, and confirms the system returned to its prior state —
> against real cloud infrastructure, not mocks.
>
> "We have rollbacks" is a belief. Ours is a guarantee.

This is the competitive differentiator that no other platform can credibly
claim. It belongs on the page.

---

### AI Safety Section — Strengthen with OS Framing

The current AI safety section is good. Add the OS framing:

> **Every AI safety problem with infrastructure access is a missing
> operating system problem.**
>
> An AI agent with direct cloud credentials is a process running without
> a kernel. It can read anything it can reach, write anywhere it has
> permission, and delete what it was never supposed to touch.
>
> Nexplane is the kernel. The agent proposes. The control plane validates,
> approves, executes, verifies — and can always undo. The agent never
> touches infrastructure directly.

Reference PocketOS and Kiro incidents if comfortable — these are public
and they're the argument in concrete form.

---

### Founder Section — Add Credibility

Currently absent. Add a brief founder section:

> **Built by someone who lived this problem four times.**
>
> John Terrill has been a CISO four times across financial services,
> media, and enterprise organizations. He's watched the same structural
> problem repeat at every one: security teams who know exactly what needs
> to happen, and no safe path to execute it.
>
> Before Nexplane, he was at Drawbridge Networks, building network
> microsegmentation at the desktop and server level — work that may have
> been a decade early. Nexplane is the same conviction, better timing.
>
> "Security teams need to be empowered to solve their own problems and
> not constantly creating work for other people."

---

### Early Access / Design Partner Section — Make the Ask Active

**Current CTA:** "Get early access" — passive, generic.

**Proposed:** Make the design partner ask explicit.

> **We're looking for design partners, not customers.**
>
> We're onboarding a small number of security and infrastructure teams
> who want to run Nexplane against their real environment and help shape
> what gets built next. No sales process. No contract. Direct access to
> the founding team.
>
> Three things we're actively trying to learn:
> - What actions and problems does the platform not yet solve?
> - What's the right model for your organization — self-hosted or managed?
> - Does Nexplane give your leadership a credible answer to their AI
>   adoption mandate?
>
> If any of those resonate, we'd like to talk.
>
> [Request a conversation →]

This CTA converts better than "get early access" because it explains
what's in it for the visitor (roadmap influence, direct founding team
access) and signals that this is a collaborative relationship, not a
beta signup queue.

---

## Implementation Notes

The site is pure HTML/CSS/JS with no build step — changes are
straightforward. The repo is at github.com/youbetyourballs/nexplane-site.

**What to change first (highest impact, lowest effort):**
1. Add the problem section (incentive misalignment) — pure copy addition
2. Update the hero headline — one line change
3. Add the live smoke test proof point to the rollback section
4. Update the early access CTA to the design partner framing

**What to change second:**
5. Add the founder section
6. Add the OS abstraction section
7. Strengthen the AI safety section with OS framing + incident references

**What to consider for a future redesign:**
- The static HTML approach is fine for now but will be limiting once
  there's a blog, changelog, or doc site. Consider migrating to a
  static site generator (Astro, Next.js) when content volume warrants it.
- A newsletter signup capture should be added now — even before the
  newsletter exists.

---

## Voice Notes for Website Copy

Same voice as LinkedIn — direct, specific, no vendor-speak. But website
copy can be slightly more structured than LinkedIn posts since visitors
are reading with intent rather than scrolling a feed.

Avoid: "leverage," "solution," "paradigm," "seamlessly," "best-in-class"
Use: specific numbers, concrete scenarios, named failure modes, real incidents

The credibility comes from specificity. "80+ change types" is credible.
"Comprehensive change management" is not.
