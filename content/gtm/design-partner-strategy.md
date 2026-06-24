# Nexplane Design Partner Strategy
# Content + In-Person Events Unified Around Three Research Questions

Created: 2026-06-16

---

## Core Framing: The Operating System for Infrastructure Change

From John's time as a graduate CS professor at NYU teaching operating systems.
This is the clearest explanation of what Nexplane is and why the category exists.
Use it in design partner conversations, the pitch, and the website.

An OS is an abstraction layer and resource manager — it standardizes
hardware-specific protocols and enforces safety boundaries. You don't write
to the disk controller. You write to the OS API.

Nexplane is the same idea one layer up. Cloud/OS-agnostic primitives —
rotate credential, isolate host, apply policy, rollback — abstracted away
from whether you're on AWS or GCP, AD or Okta, Vault or SSM. You define
a change request, not a boto3 script.

The abstraction serves the same two purposes an OS does:
- **Safety** — no direct cloud API access; everything goes through the
  control plane which enforces rollback, blast radius, and approval gates
- **Portability** — define once, execute against any supported connector

For AI conversations: every AI safety problem with infrastructure access
is a missing OS problem. The AI agent is a process. It needs a kernel.
That's Nexplane.

For the open source conversation: Linux is open source. Red Hat charges
for the certified, guaranteed version. The abstraction is free; the
guarantee is the product.

---

## The Goal

Design partners who will run Nexplane against real infrastructure and give
structured feedback. Not paying customers. Not a sales motion.

The output needed from design partners:
1. **Coverage gaps** — what actions and problems does the platform not yet solve?
2. **Distribution model** — what is the interest and tolerance for open source
   vs. managed/SaaS? Does self-hosting vs. managed change the decision?
3. **Budget unlock** — are budgets a problem, or does Nexplane give
   infrastructure leaders a credible answer to leadership about how they're
   embracing AI responsibly?

Every content piece and every in-person event should be designed to surface
one or more of these three answers. This replaces the generic "awareness and
visibility" framing from the earlier content strategy.

---

## The Three Research Questions in Detail

### Question 1: Coverage Gaps
**What we need to learn:** Where does Nexplane hit its edges? What does a
practitioner try to do that the platform doesn't support — a connector that
doesn't exist, an action that isn't modeled, a workflow that doesn't fit
the CR lifecycle?

**How this surfaces:** Practitioners who actually run it against their
infrastructure will find the edges within the first few sessions. The
design partner engagement should include a structured debrief: "what did
you try that didn't work, and what workaround did you use?"

**Content that attracts this audience:** The Back to Basics series and
the Execution Gap post. People who feel the specific pain of "I know what
I need to do and I can't execute it safely" are the right design partners
for this question. They arrive with concrete use cases.

**Events that surface this:** CoffeeOps and DOXNYC — conversation format
where practitioners describe their actual environment. Ask "what's the
hardest security change you've had to make in the last year" and listen
for the shape of the problem.

---

### Question 2: Open Source vs. Managed
**What we need to learn:** Does the SRE/sysadmin crowd want to self-host?
Is that a strong enough preference that a closed SaaS would be a blocker?
Does their org's security posture allow a SaaS product with infrastructure
credentials? Would managed remove enough operational burden to justify
the cost? Is open core (self-host free, managed paid) the right model?

**How this surfaces:** This is a conversation question, not a content
question. It won't come up in LinkedIn comments. It comes up in person
when someone asks "can I run this myself" or "where do my credentials go."

**Content that attracts this audience:** The AI safety harness posts will
attract practitioners who are thinking carefully about what gets access to
what. These are the people who will have strong opinions about credential
handling and self-hosting.

**Events that surface this:** NYLUG and Unigroup — these communities have
the strongest open source instincts and will ask the self-hosting question
directly. Red Hat user group similarly. Ask explicitly: "if this were open
source, would you run it yourself, and what would stop you?"

---

### Question 3: Budget Unlock via AI Mandate
**What we need to learn:** Are infrastructure and security leaders being
asked by their organizations to demonstrate AI adoption? Is Nexplane a
credible answer to that mandate — "here's how we're using AI agents on
infrastructure safely"? Does that framing unlock budget that wouldn't
otherwise exist for a security execution platform?

**Why this matters:** If the answer is yes, the buying motion is completely
different. Instead of competing for security budget against SOAR and ITSM,
Nexplane competes for AI/innovation budget — a category that is currently
flush with board-level attention and mandate. The pitch isn't "buy this
security tool," it's "here's your answer when the board asks how you're
embracing AI."

**How this surfaces:** Ask directly in conversations: "Is your leadership
asking you how you're using AI? What's your current answer?" If the room
goes quiet or uncomfortable, that's signal. If people start talking about
internal mandates and pressure from above, that's the confirmation.

**Content that attracts this audience:** The AI Safety Harness posts (both
parts) and the personal VPN story. Leaders who are feeling pressure to adopt
AI but are worried about what happens when it goes wrong are exactly the
audience for "here's a safe execution layer for AI agents."

**Events that surface this:** SRE Tech Talks and Uptime NY — these crowds
are closest to the AI/infrastructure intersection and most likely to be
feeling organizational pressure on the AI mandate question.

---

## Unified Content + Events Calendar

The content and events work together. Each post is designed to generate
inbound from people who feel a specific pain. Each event is designed to
have the conversations that surface the three research questions. Neither
works as well without the other.

### Phase 1: Establish the Problem + Begin Attending (Weeks 1–4)

**Content:** Posts 1-3 (System Produces Bad Outcomes, Incentive Mismatch,
Execution Gap). No product mention. Pure observation. The CTA at the end
of these posts is: "if this sounds familiar, I'd like to hear how your
org handles it." That's a design partner opener, not a sales pitch.

**Events:** Attend CoffeeOps, NYLUG, DOXNYC as a listener. Don't pitch.
Introduce yourself as someone who has been a CISO four times and is working
on something in the security execution space. Let curiosity do the work.

**Research questions targeted:** Primarily Q1 (coverage gaps) — the
conversations will surface what people are trying to do that they can't.

---

### Phase 2: Establish Credibility + First Conversations (Weeks 5–7)

**Content:** Posts 4-5 (Founder Credibility, Origin Story) and Posts 6-7
(SOAR/ITSM/Consultants, Fit Over Correctness). The credibility posts give
people context for who you are before you ask them to be a design partner.
The competitive posts help practitioners articulate why their current tools
don't solve the problem — which is the first step toward wanting something
that does.

**Events:** Begin having direct conversations at events. After a few
attendances, people will ask "so what are you building?" — that's the
opening. The answer is: "a safe execution layer for security and
infrastructure changes. I'm looking for a handful of practitioners who
want to run it against their real environment and tell me what's missing."

**Research questions targeted:** Q1 and Q2. The "what are you building"
conversation will quickly surface whether self-hosting is important.

---

### Phase 3: The Framework Posts + Design Partner Recruiting (Weeks 8–11)

**Content:** Resilience, Rollback Guarantee, Chesterton's Fence, CMDB/Asset
Inventory. These posts are the most practitioner-resonant — they describe
specific, recognizable problems with specific, recognizable failure modes.
People who respond to these posts are self-identifying as having the problem.

**Events:** Propose a talk to DOXNYC. Talk title: "Why Your Rollback Won't
Work When You Need It." End the talk with: "I'm building something to solve
this and I'm looking for three or four practitioners who want to help shape
it. If you're interested, find me afterward." That's a design partner ask
that doesn't feel like a sales pitch.

**LinkedIn CTA shift:** After the Rollback Guarantee post, add a line:
"I'm looking for a handful of infrastructure and security practitioners
who want early access to run this against their environment. DM me if
that's you."

**Research questions targeted:** All three. The rollback talk will surface
Q1 and Q2 in Q&A. The CTA will surface Q3 — people who respond to the
"early access" ask and mention AI mandates are signaling budget unlock.

---

### Phase 4: Back to Basics + Technical Credibility (Weeks 8–14)

**Content:** The 7-post Back to Basics series, alternating with Phase 3
framework posts. Each post is a specific, searchable, practitioner-level
problem. These build SEO and attract practitioners who are actively
searching for solutions to the exact problems being described.

**Events:** Propose a talk to NYC SRE Tech Talks. Technical hook:
"The Syscall You Don't Know About" or "Microsegmentation Without Clean
Network Metadata." End with the same design partner ask.

**Reddit/HN:** Begin sharing Back to Basics posts in r/sysadmin and
r/netsec when directly relevant to active discussions. Don't promote —
contribute. The posts are good enough to stand on their own.

**Research questions targeted:** Primarily Q1. Practitioners who respond
to the specific layer posts (seccomp, segmentation, CMDB) will arrive with
concrete use cases that immediately test the platform's coverage.

---

### Phase 5: The AI Angle + Budget Unlock Signal (Weeks 15–16)

**Content:** AI Safety Harness Part 1 (incidents) and Part 2 (technical
mechanism with code). These are the highest-signal posts for Q3 — the
people who respond to these are the ones feeling pressure to adopt AI
responsibly.

**Events:** By this point you should have design partners engaged. The
events shift from recruiting to learning — bring design partner feedback
back to the community as observations ("here's what practitioners are
running into when they try to execute security changes with AI agents
involved").

**LinkedIn CTA:** After the AI posts, the ask shifts to "if your
organization is navigating the AI + infrastructure access question and
you don't have a safe answer yet, let's talk."

**Research questions targeted:** Primarily Q3. The AI mandate framing
will surface whether budget unlock is real.

---

## Design Partner Engagement Structure

Once someone expresses interest, the engagement has three parts:

**1. Intake conversation (30 min)**
- What does your infrastructure look like? (cloud, hybrid, on-prem)
- What are the security changes that sit undone in your backlog?
- What's your current process for executing a change like that?
- What would make you trust a tool to execute it with less oversight?
- Are you self-hosting everything or open to managed?
- Is your organization asking you how you're adopting AI? What's your answer?

**2. Live run (ongoing)**
- Access to Nexplane against their real environment
- Weekly or bi-weekly check-in: what did you try, what worked, what didn't
- Structured gap logging: connector missing, action not modeled, workflow
  doesn't fit, rollback didn't work as expected

**3. Structured debrief (60 min, after 4-6 weeks)**
- Coverage gaps: what's on your list that we don't solve?
- Model preference: would you self-host this? What would change if you could?
- AI angle: would this be your answer to "how is your team using AI safely"?
- Pricing instinct: what would this be worth if it worked exactly as you
  needed it to?

---

## What Design Partner Success Looks Like

After 90 days and 4-6 design partners:

**Q1 answered:** A prioritized list of coverage gaps — specific connectors,
actions, or workflow patterns that practitioners hit repeatedly. This
becomes the next product roadmap.

**Q2 answered:** A clear read on whether open source / self-host is a
strong enough preference to affect distribution. If 4 of 6 partners say
"I'd never run a SaaS with my cloud credentials," that's a signal. If
they shrug and say "managed is fine as long as the credentials are
scoped correctly," that's a different signal.

**Q3 answered:** Whether the AI mandate framing unlocks budget. If
design partners start saying "I could pitch this internally as our AI
infrastructure safety answer," that's the confirmation. If they say
"our AI budget is separate and I'd never get security execution funded
from it," that's also useful.

---

## Event Schedule — What's Confirmed

| Event | Date | Location | Action |
|---|---|---|---|
| NYC SRE Tech Talks | June 23, 2026, 6:30pm | Google NYC, 111 8th Ave (8th Ave lobby, corner of 15th St) | Register immediately — one week out |
| CoffeeOps | TBD — monitor Meetup | Midtown Manhattan | Join group, attend when posted |
| DOXNYC | TBD — monitor Meetup | TBD | Join group, propose talk after 1-2 attendances |
| NYLUG | 2nd Thursday monthly | TBD — requires Meetup signup | Join group, attend |
| Uptime NY | TBD — monitor Meetup | TBD | Join group |
| Platform Engineers NYC | TBD — monitor Meetup | TBD | Join group |
| Red Hat User Group | TBD — monitor Meetup | TBD | Join group |
| Unigroup | Virtual, odd months | Virtual (Zoom) | Subscribe at unigroup.org |

**Reminder set for June 17, 2pm:** Join all Meetup groups and register
for June 23 SRE Tech Talks.

---

## Notes on Voice at Events

The founder story is the asset. Four times as a CISO. Drawbridge Networks.
An AI put my desktop on the public internet while I was building a platform
to prevent exactly that.

That story earns the right to say "I'm looking for practitioners who want
to help me build this." It doesn't sound like recruiting. It sounds like
someone who has lived the problem and is serious about solving it.

The ask is never "try my product." It's always "help me understand where
the edges are." That's a fundamentally different relationship — it's
collaborative, not transactional. Design partners who feel like collaborators
become advocates. Customers who feel like early adopters become churned users.
