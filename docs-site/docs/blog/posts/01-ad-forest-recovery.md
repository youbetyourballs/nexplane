---
title: "What actually happens when you try to recover an AD forest after ransomware"
date: 2026-05-16
category: technical
---

In May 2017, WannaCry hit the NHS. Within hours, hospitals were diverting ambulances. The immediate story was ransomware encrypting files. The harder story — the one that took months to fully reckon with — was what the attack did to Active Directory.

This post is about that harder story, and why it matters for how you think about recovery.

## The backup problem nobody talks about

When most organizations hit a ransomware event, the first instinct is to reach for backups. Veeam, Commvault, Veritas — whatever's in the environment. Those tools do exactly what they're designed to do: they restore data. Encrypted files become readable files again.

But in a domain environment, data restoration and trust restoration are not the same thing.

Active Directory is the trust anchor for everything in a Windows environment. Every service account credential, every GPO, every Kerberos ticket, every certificate trust chain — it all flows from your domain controllers. When ransomware has been in your environment for days or weeks before detonation (and it almost always has), your AD is contaminated. Not necessarily encrypted. Contaminated. The attacker may have created backdoor accounts, modified GPOs to disable logging, established persistence via DCSync-capable service accounts, or tampered with ntds.dit directly.

Restoring your AD from a backup doesn't fix any of that. It restores a trusted copy of a compromised system. The backup is forensically clean in the sense that it decrypts — but it contains the attacker's modifications, the backdoor accounts, the weakened GPOs. You've just put the infection back in place at a known-good point in time.

## The tiered recovery model

Practitioners who've done this before talk about a tiered recovery order, and it's not optional. It's a sequencing constraint.

**Tier 0** is your domain controllers, PKI infrastructure, and ADFS. These have to come up first, and they have to be clean. Not restored — rebuilt. That means:

- Deploying a fresh OS image on new or wiped hardware
- Restoring AD data selectively: you need SYSVOL, you need the database, but you need to audit what you're restoring. Not every GPO gets to come back. Not every service account gets to come back.
- Standing up PKI from scratch if your CRL infrastructure is suspect

**Tier 1** is member servers. Application servers, file servers, SQL instances. These cannot be rejoined to the domain until Tier 0 is complete and integrity-verified. This is the step that organizations skip under pressure. The machines are sitting there, the business is screaming, and someone makes the call to start rejoining servers before the DCs are clean. That decision re-infects everything you just brought back up.

**Tier 2** is workstations. Last in, because they're the highest-count, lowest-priority systems, and because by the time you're rejoining workstations you need the authentication infrastructure stable enough to handle the load.

## What "clean DC" actually means

Rebuilding a domain controller clean means starting from a known-good image — ideally an AMI or disk image captured before the incident window — and then restoring domain data, not the full machine. The distinction matters. You want the AD database (ntds.dit) because it contains the objects and structure of your domain, but you want it restored onto infrastructure that hasn't been touched by the attacker.

Before you bring Tier 1 machines up against it, you verify:

- SYSVOL replication is healthy (DFSR, not FRS — if you're still on FRS in 2024, that's a separate problem)
- No unexpected privileged accounts exist in Domain Admins, Enterprise Admins, Schema Admins
- GPOs are audited and compared against a known baseline
- KRBTGT has been rotated twice (once for the rollover, once to invalidate any Golden Tickets)
- DNS is clean and resolving correctly

This is not a thirty-minute process. In large environments it takes days. That's the actual recovery timeline, and anyone who tells you otherwise hasn't done it at scale.

## The primitive that's missing

What the WannaCry recovery experience exposed — and what subsequent incidents have confirmed — is that organizations are missing two distinct recovery artifacts. They have one: clean data. They're missing the other: clean infrastructure.

Clean data means your backups are intact, your logs are preserved, your database snapshots are recoverable.

Clean infrastructure means you have a known-good, pre-incident image of your domain controllers that can be deployed at a moment's notice, with a documented, tested process for restoring AD data onto it and then verifying integrity before anything joins.

Most backup strategies cover the first. Almost none have a tested, runbook-driven answer to the second. The AD forest rebuild is improvised in the middle of an incident, under pressure, by whoever happens to be available.

## What a structured recovery workflow looks like

Done well, the sequence is:

1. Deploy clean DC image from pre-incident snapshot
2. Restore AD data (ntds.dit, SYSVOL) with integrity checks
3. Audit privileged accounts and GPOs before enabling replication
4. Rotate KRBTGT twice with the prescribed delay between rotations
5. Verify DNS resolution and replication across all DCs
6. DNS cutover for the environment
7. Begin Tier 1 rejoin with monitoring for authentication failures
8. Tier 2 rejoin after Tier 1 is stable

That's the skeleton. Each step has sub-steps, and each sub-step has a verification gate before the next one starts. The organizations that recover fastest from AD-level ransomware aren't the ones with the most compute — they're the ones who've practiced this sequence and have the runbook written down before they need it.

The WannaCry aftermath showed what happens when you don't. Some NHS trusts took months to fully restore operations. The encryption was reversible with backups. The trust problem wasn't.
