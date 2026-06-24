# NYC Event & Community Schedule
# Sysadmin / SRE / Linux / Infrastructure Audience

Created: 2026-06-16

---

## Target Groups — Active NYC Communities

### SRE / Site Reliability

**New York Site Reliability Engineering Tech Talks**
- Meetup: https://www.meetup.com/new-york-site-reliability-engineering-tech-talks/
- Format: Lightning talks + networking, sponsored by Google NY
- Audience: SREs, platform engineers, infrastructure leads
- Action: Submit a talk proposal. "Why Your Rollback Won't Work When You Need It"
  is a natural fit — operational, credible, not a sales pitch.

**Google SRE NY**
- Meetup Pro group, ~2,923 members across 2 groups globally
- URL: https://www.meetup.com/pro/google-sre-ny/
- Audience: Google-adjacent SRE practitioners, high technical bar
- Action: Attend first to understand the audience before pitching a talk

**NYC Monitoring and Observability**
- Meetup: https://www.meetup.com/nyc-metrics-and-monitoring/
- Audience: SREs, DevOps, anyone running observability stacks
- Angle: Log agent deployment friction, SIEM coverage gaps — directly from
  the Back to Basics series. Practitioner-level, not a product talk.

**Uptime New York**
- Meetup: https://www.meetup.com/uptime-new-york/
- Format: In-person, engineers building modern infrastructure
- Covers: DevOps, SRE, platform, cloud, AI/ML
- Audience: Broad infrastructure engineering crowd — good for first exposure

---

### DevOps / Platform Engineering

**DevOps Exchange NYC (DOXNYC)**
- Meetup: https://www.meetup.com/doxnyc/
- Format: 3 short presentations by invited speakers + networking over drinks
- Audience: DevOps, SRE, Cloud, Software Engineers
- Action: Strong venue for a talk. "War stories" format fits the origin story
  and the Chesterton's Fence angle well.

**Platform Engineers NYC**
- Meetup: https://www.meetup.com/platform-engineers-nyc/
- Audience: Internal tooling, platform teams, internal developer platforms
- Angle: The security/platform team handoff problem. Nexplane as the
  execution layer platform teams can trust security teams to use safely.

**NYC CoffeeOps**
- Meetup: https://www.meetup.com/nyc-coffeeops/
- Format: Lean coffee — attendee-driven discussion, no formal presentations
- Location: Midtown Manhattan
- Covers: Platform engineering, dev+ops, automation, infrastructure as code
- Action: Attend and participate. Great for genuine conversation, not pitching.
  Bring the CMDB / asset inventory problem as a discussion topic.

**DevOps and Drinks**
- Meetup: https://www.meetup.com/devops-docker-and-beers/
- Topics: Docker, Kubernetes, AWS migrations, configuration management
- Audience: Hands-on DevOps engineers
- Angle: The OS upgrade / containerization angle from Back to Basics fits here

**DevOps Exchange NYC**
- URL: https://www.meetup.com/DevOps-Online-Meetup-NYC/
- General DevOps audience, all skill levels

---

### Linux / Unix Communities

**NYLUG — New York Linux Users Group**
- Meetup: https://www.meetup.com/nylug-meetings/
- Website: http://www.nylug.org/
- Format: Monthly meetings, usually second Thursday of the month
- Free and open to the public (RSVP required)
- Audience: Linux practitioners, open source community, technical depth
- Action: Attend a few meetings first. This community has been around for
  decades and has low tolerance for vendor pitches. Earn credibility
  through genuine participation before proposing a talk.
- Best talk angle: The seccomp / syscall monitoring problem from Back to Basics.
  Technical, Linux-specific, no product angle needed.

**Unigroup of New York**
- Website: http://www.unigroup.org/
- Format: Virtual meetings scheduled Jan, Mar, May 2026 (check for more)
- Covers: Unix/Linux/BSD/Solaris/AIX — broad Unix practitioner audience
- Audience: Senior practitioners, long-tenured infrastructure professionals
- Action: Virtual format lowers barrier to participation. Good early target.

**New York Red Hat User Group**
- Meetup: https://www.meetup.com/nyrhug-new-york-red-hat-users-group/
- Audience: Red Hat / enterprise Linux users — sysadmin and ops heavy
- Angle: OS hardening, SELinux, audit policies — directly relevant

---

## Talk Topics by Audience

These are the talk angles most likely to land with each community.
None of them are product demos — all are practitioner observations.

| Talk Title | Best Venues |
|---|---|
| Why Your Rollback Won't Work When You Need It | DOXNYC, SRE Tech Talks, Uptime NY |
| Microsegmentation Without Clean Network Metadata | NYLUG, Platform Engineers NYC |
| The Syscall You Don't Know About (seccomp in practice) | NYLUG, Unigroup, Red Hat UG |
| The CMDB Nobody Wants to Own | CoffeeOps, Platform Engineers NYC, DOXNYC |
| Don't Give Your AI Agent Cloud Credentials | SRE Tech Talks, Uptime NY, DOXNYC |
| OS Upgrades and the App Nobody Will Touch | NYLUG, Red Hat UG, DevOps and Drinks |

---

## Engagement Strategy by Community Type

**Linux/Unix groups (NYLUG, Unigroup, Red Hat UG)**
These communities have been around for decades and have sharp radar for
vendor pitches. The entry strategy is: attend, contribute to discussions,
earn credibility. Then propose a deeply technical talk with no product
mention. The Nexplane story can come out naturally in conversation when
someone asks "what are you working on" — not in the talk itself.

**SRE groups (Google SRE NY, NYC SRE Tech Talks)**
Higher tolerance for tooling discussion but the audience is sophisticated.
Talks need a strong technical hook. The rollback and observability angles
are strongest here. Avoid slides that look like marketing.

**DevOps/Platform groups (DOXNYC, Platform Engineers, CoffeeOps)**
Most receptive to problem-framing talks. The incentive mismatch and
execution gap angles resonate with practitioners who live the
security/infra handoff every day. Can reference Nexplane directly in
Q&A without it feeling like a pitch.

---

## Priority Order for First 90 Days

1. **Attend** CoffeeOps, NYLUG, and DOXNYC — listen before speaking
2. **Propose a talk** to DOXNYC — most receptive to practitioner war stories,
   lowest barrier to getting on stage
3. **Propose a talk** to NYC SRE Tech Talks — higher bar, needs a strong
   technical hook, but the audience is exactly right
4. **Participate** in NYLUG discussions — build genuine credibility before
   any talk pitch
5. **Submit** to Uptime New York — broad infrastructure audience, good
   for awareness even if not the deepest technical crowd

---

## Strategic Note on the SRE/Sysadmin Buyer

The shift toward sysadmin/SRE as primary buyer vs. security team is
worth developing. The argument:

Security teams identify the problem and create urgency, but they often
don't own the infrastructure and can't buy tools that require infra
cooperation to deploy. SREs and platform engineers own the infrastructure,
feel the pain of security requests landing in their queue, and have
budget authority for tooling that makes their lives better.

The pitch to an SRE is different from the pitch to a CISO:
- CISO: "finally execute your security roadmap without waiting on infra"
- SRE: "stop being the bottleneck for every security change — give security
  a governed path that doesn't require you to babysit every request"

Both are true. The SRE framing may unlock faster adoption because it
removes friction for the person who would otherwise block deployment.
Worth testing both framings in the wild at these meetups.
