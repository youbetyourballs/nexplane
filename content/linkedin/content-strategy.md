# Nexplane Content Strategy
# Narrative Progression, Release Schedule, and Channel Plan

Created: 2026-05-30

---

## The Narrative Arc

The content has a deliberate progression. Each phase builds on the last.
Publishing out of order weakens the argument — readers who arrive mid-series
won't have the foundation.

### Phase 1 — Establish the Problem (Weeks 1–3)
Goal: make the reader feel the problem before they hear about the solution.
These posts stand alone as observations. No product pitch. Pure insight.

The audience — CISOs, security engineers, VP Infra, DevOps leads — has lived
this. The posts should feel like someone finally articulating something they've
always known but never heard said clearly.

**Post 1: The System Produces Bad Outcomes, Not Bad People**
The root cause thesis. Everyone is rational. The system produces bad security
outcomes anyway. Sets the frame for everything that follows.

**Post 2: The Incentive Mismatch — They're Not Your Enemy**
Names the structural dynamic. Infra teams aren't wrong to protect uptime.
The CISO who understands this gets further than the one who fights it.

**Post 3: The Ticket Graveyard / Execution Gap**
Makes the problem concrete and personal. Every reader has this list.
The problem has never been what to do — it's been who can safely do it.

---

### Phase 2 — Establish Credibility (Weeks 4–5)
Goal: earn the right to propose a solution. Personal, specific, non-promotional.

**Post 4: Founder Credibility — The Same Bet, Twice**
Drawbridge Networks, 4x CISO, pattern recognition. Making the same bet again
because the timing is finally right.

**Post 5: Origin Story — "Someone Told Me They Had Rollbacks. They Didn't."**
The founding insight in one story. Self-reported safety is not safety.
Keep anonymous. Works better that way.

---

### Phase 3 — Name What's Missing (Weeks 6–7)
Goal: show that existing tools don't solve this — without being a product pitch.
This is the "why nothing works" phase.

**Post 6: SOAR / ITSM / Consultants — Nobody Completes The Roadmap**
Three sharp contrasts. Competitive positioning that reads as industry observation.

**Post 7: Fit Over Correctness — The Security Tool Graveyard**
Technically correct controls that don't fit the organization don't get partially
adopted — they get removed. A removed control creates false confidence.

---

### Phase 4 — The Fundamentals (Weeks 8–14, Back to Basics series)
Goal: demonstrate depth and breadth. One post per layer, weekly.
Each is standalone but the series builds a complete picture.
This phase runs in parallel with Phase 5 — alternate weeks.

**B1: Network — Microsegmentation**
**B2: OS — Kernel Upgrades and the App Nobody Will Touch**
**B3: Application — Seccomp and the Syscall You Don't Know About**
**B4: Identity — Least Privilege Requires a Map Nobody Has**
**B5: Secrets — Rotation Requires an Inventory You Don't Have**
**B6: Observability — The Log Agent That Never Got Deployed**
**B7: Cloud — The Security Group Nobody Will Tighten**

---

### Phase 5 — The Framework (Weeks 8–14, alternating with Back to Basics)
Goal: introduce the conceptual vocabulary Nexplane is built on.
These posts introduce "resilience," "rollback guarantee," and Chesterton's Fence
as ideas — not as product features.

**Post 8: Resilience Is The Right Frame**
Survive, mitigate, recover. This word should enter the vocabulary.
Anchored to Code Spaces and TravelEx — companies that didn't survive.

**Post 9: The Rollback Guarantee — "We Have Rollbacks" Is Not A Rollback**
Origin story + PocketOS + Code Spaces. The guarantee has to be structural.
Rollback is harder than most people model.

**Post 10: Chesterton's Fence**
Don't tear down what you don't understand. Audit trails as institutional memory.

**Post 11: The Asset Inventory Problem**
Nobody wants to own the CMDB. Every downstream control is degraded.
The brownfield problem and why velocity eventually dies there.

---

### Phase 6 — The AI Angle (Weeks 15–16)
Goal: stake a position on the AI infrastructure access debate with technical depth.
By this point the audience trusts the voice. The argument lands harder.

**Post 12: AI Safety Harness Part 1 — The Incidents**
PocketOS, Kiro, personal VPN story. Direct infrastructure access + no approval
gate = catastrophic outcomes from non-malicious agents.

**Post 13: AI Safety Harness Part 2 — The Mechanism (with code)**
Why typed interfaces don't protect you. Prompt injection, reasoning escape,
memory persistence. The architectural answer vs. the prompting answer.

---

### Reactive / Timely (Hold in draft, publish on trigger)
**Exploits and the News Cycle**
Hold this post and publish within 48 hours of the next major CVE/exploit
announcement. The argument: exploits change headlines, not fundamentals.
Defenders who assumed exploits existed already had a different morning.

---

## Suggested Cadence

2 posts per week is sustainable and builds momentum without burning out the
audience. Mix series and standalone to keep variety.

| Week | Post |
|------|------|
| 1    | The System Produces Bad Outcomes (Phase 1) |
| 1    | The Incentive Mismatch (Phase 1) |
| 2    | The Execution Gap (Phase 1) |
| 2    | Founder Credibility (Phase 2) |
| 3    | Origin Story — Rollbacks (Phase 2) |
| 3    | SOAR / ITSM / Consultants (Phase 3) |
| 4    | Fit Over Correctness (Phase 3) |
| 4    | Resilience Is The Right Frame (Phase 5) |
| 5    | Back to Basics: Network (Phase 4) |
| 5    | The Rollback Guarantee (Phase 5) |
| 6    | Back to Basics: OS (Phase 4) |
| 6    | Chesterton's Fence (Phase 5) |
| 7    | Back to Basics: Application (Phase 4) |
| 7    | Asset Inventory / CMDB (Phase 5) |
| 8    | Back to Basics: Identity (Phase 4) |
| 8    | Back to Basics: Secrets (Phase 4) |
| 9    | Back to Basics: Observability (Phase 4) |
| 9    | Back to Basics: Cloud (Phase 4) |
| 10   | AI Safety Harness Part 1 (Phase 6) |
| 10   | AI Safety Harness Part 2 (Phase 6) |
| Hold | Exploits / News Cycle — publish on next major CVE |

Total: 20 planned posts + 1 reactive. Approximately 10 weeks of content at
2 posts/week. Enough runway to be building the next batch while publishing.

---

## Beyond LinkedIn Posts

LinkedIn is the right primary channel for the ICP (CISOs, VP Infra, security
practitioners) but it shouldn't be the only surface. Each content type serves
a different purpose.

---

### 1. Long-Form Blog Posts on nexplane.ai

**Purpose:** SEO, depth, permanence. LinkedIn posts disappear in feeds.
Blog posts are discoverable months later via search.

**Approach:** The LinkedIn posts are the distillation. The blog post is the
full argument with more nuance, more examples, and internal links.
Each LinkedIn post should have a corresponding blog post that goes 3-5x deeper.

**Priority posts to expand first:**
- The Rollback Guarantee (most important trust-builder)
- AI Safety Harness Part 2 (technical, SEO-friendly for "prompt injection" etc.)
- Back to Basics series (each layer = a targetable search query)
- Why Nexplane exists (founder narrative, long-form)

**SEO angles worth targeting:**
- "security team execution platform"
- "how to safely rotate credentials in production"
- "microsegmentation without clean network metadata"
- "AI agent infrastructure access risks"
- "prompt injection infrastructure"
- "security change management rollback"

---

### 2. Technical Posts for Developer / Security Practitioner Audiences

**Purpose:** Credibility with practitioners, not just buyers.
Different voice than LinkedIn — more technical, more peer-to-peer.

**Channels:** Hacker News (Show HN for launch, Ask HN for topic posts),
dev.to, security blogs, SANS reading room style long-form.

**Best candidates from the backlog:**
- AI Safety Harness Part 2 (the code examples make this HN-worthy)
- How Nexplane tests its own rollback against live infrastructure (engineering post)
- The CMDB problem as a security force multiplier (practitioner angle)
- Seccomp policies in practice — the discovery problem (very specific, very searchable)

---

### 3. Podcast Appearances

**Purpose:** Reaches CISO and security leadership audiences who don't read
blogs but do listen on commutes. Builds personal brand alongside Nexplane brand.

**Good fit shows (security/infra leadership audience):**
- Risky Business (Patrick Gray — top security podcast)
- CISO Series / CISO/Security Vendor Relationship Podcast
- Darknet Diaries (storytelling angle — origin story would fit)
- Security Now (technical depth)
- The Cloud Security Podcast
- Screaming in the Cloud (AWS/cloud practitioner audience)

**Pitch angle:** The founder narrative (4x CISO, Drawbridge Networks,
built the safety harness while an AI put his desktop on the internet)
is a strong podcast pitch — it's a story, not a product demo.

---

### 4. Conference Speaking

**Purpose:** Credibility, visibility with the exact buying audience,
content that can be repurposed as blog posts and LinkedIn posts.

**Target conferences:**
- RSA Conference (large, CISO audience)
- BSides events (practitioner credibility)
- fwd:cloudsec (cloud security practitioners)
- KubeCon / CloudNativeCon (infrastructure angle)
- SANS summits (technical depth welcome)

**Best talk angles from the backlog:**
- "The System Produces Bad Outcomes, Not Bad People" — the incentive
  misalignment talk. Would land well at RSA or a CISO summit.
- "Why Your Rollback Won't Work When You Need It" — technical, demo-able,
  anchored to real incidents.
- "Don't Give AI Your Cloud Keys" — timely, technical, demo-able with
  the code injection examples.

---

### 5. Short-Form Video (LinkedIn / YouTube Shorts)

**Purpose:** Expands reach to people who don't read. Talking-head format
works for founder-led content — low production cost, high authenticity.

**Format:** 60-90 second clips. One idea per video. Same content as the
LinkedIn posts, spoken instead of written. Can be cut from a longer recording.

**Best candidates for video first:**
- Origin story (narrative, personal, no slides needed)
- The incentive mismatch (explain the infra/security dynamic)
- "Someone told me they had rollbacks" (punchy, memorable)

---

### 6. Newsletter

**Purpose:** Owned audience that doesn't depend on LinkedIn algorithm.
Captures people who want to follow the thinking but aren't LinkedIn-active.

**Format:** Weekly or bi-weekly. Could be:
- A curated version of the week's LinkedIn post(s) with extra context
- A "what I'm thinking about this week in security operations" format
- Eventually: product updates + content in one place for early customers

**Platform:** Substack or Beehiiv. Both are free to start, portable,
and have built-in discovery.

**When to start:** Once there are 4-6 posts published and a point of view
is established. Don't start a newsletter with nothing to show.

---

### 7. Reddit / Community Engagement

**Purpose:** Practitioner credibility. The people on r/netsec, r/sysadmin,
r/devops are the practitioners who influence buying decisions even if they
don't make them. Being known and respected there matters.

**Approach:** Contribute genuinely, not as promotion. Answer questions.
Share the blog posts when directly relevant to a discussion — not as spam.
The Back to Basics series posts are natural fits for practitioner communities.

**Relevant communities:**
- r/netsec (security practitioners)
- r/sysadmin (infrastructure ops)
- r/devops (platform/DevOps engineers)
- r/securityCTF (future — when there's more technical content)
- Hacker News (Show HN for product launch)

---

### 8. Analyst and Press Briefings

**Purpose:** Garners third-party validation. Analysts at Gartner, Forrester,
IDC shape how buyers think about categories. Being in an analyst report
matters more than most marketing spend at the enterprise level.

**When:** After product is in customer hands and there's a story to tell.
Too early to pitch analysts without proof points.

**Prep work now:** The "new category" framing (Security-Led Infrastructure
Operations) from the pitch deck should be the category name used consistently
everywhere — on the site, in posts, in pitches — so it's established when
analyst conversations happen.

---

### 9. Product Hunt Launch

**Purpose:** Developer and early adopter awareness. Not the primary ICP
but useful for early signal, feedback, and inbound interest from practitioners.

**When:** When the product is ready for public access. Prep: get upvote
commitments from network in advance, have a strong demo ready.

---

## Priority Order (What To Do First)

**The goal is design partners, not awareness.** Traditional conferences
are deprioritized. Content and in-person meetups work together to surface
practitioners who want to run Nexplane against real infrastructure.

See `F:\Nexplane\nexplane\content\gtm\design-partner-strategy.md` for the
full unified strategy. Summary:

1. **LinkedIn posts** — Phase 1 posts drafted and ready. CTA is "if this
   sounds familiar, I'd like to hear how your org handles it" — not a
   product pitch. Design partner opener.
2. **NYC meetup groups** — join all groups (reminder set for June 17, 2pm).
   Register for June 23 SRE Tech Talks at Google NYC immediately.
3. **Attend before speaking** — CoffeeOps, NYLUG, DOXNYC first as a listener.
   Earn credibility, then propose a talk.
4. **Blog posts on nexplane.ai** — expand LinkedIn posts to long-form for SEO.
5. **Newsletter capture** — add signup form to nexplane.ai now.
6. **Talk proposals** — DOXNYC first (lowest barrier), then SRE Tech Talks.
   End every talk with an explicit design partner ask.
7. **Reddit / HN** — genuine participation, Back to Basics posts when relevant.
8. **Podcast pitches** — after 4-5 posts establish the voice.
9. **Analyst briefings** — after design partners provide proof points.

## Three Design Partner Research Questions

All content and events are designed to surface answers to:

1. **Coverage gaps** — what actions/problems does the platform not yet solve?
   Attracted by: Back to Basics series, Execution Gap post, CoffeeOps conversations.

2. **Open source vs. managed** — self-host preference, credential handling
   concerns, managed vs. operational burden tradeoff.
   Surfaced by: NYLUG, Unigroup, Red Hat UG conversations — ask directly.

3. **AI mandate budget unlock** — are infrastructure leaders being asked to
   demonstrate AI adoption? Is Nexplane their credible safe answer?
   Attracted by: AI Safety Harness posts. Surfaced by asking "is your org
   asking how you're embracing AI?" at SRE Tech Talks and Uptime NY.

---

## Notes on Voice

All content — across all channels — should sound like the same person.
The LinkedIn voice is the master: direct, specific, no vendor-speak,
no "solutions" or "paradigm shifts," no fear-mongering.

The credibility comes from having lived the problem, not from claiming
to have solved it. The product is the evidence. The content is the argument.
