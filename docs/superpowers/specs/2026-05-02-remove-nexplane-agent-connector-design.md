# Remove nexplane_agent from Connectors Tab — Design Spec

**Date:** 2026-05-02
**Status:** Approved
**Scope:** Remove the `nexplane_agent` entry from the Connectors UI to eliminate the false impression that operators need to configure a connector to use the agent.

---

## Background

The `nexplane_agent` connector type appears in the Connectors tab and the Add Connector dropdown, but it plays no role in agent authentication or job dispatch. Agents authenticate via the secret generated in Settings → Agent Configuration. The connector record is a misleading artefact from the original demo seed.

## Design Decisions

- **Keep `nexplane_agent` in the ConnectorType DB enum** — removing it would require a migration and risk breaking existing records. Hiding it from the UI achieves the goal with zero data risk.
- **Remove from seed data** — the demo org should not have a `nexplane_agent` connector card after a fresh seed.
- **Hide from Add Connector dropdown** — `nexplane_agent` is filtered out of the options list in `AddConnectorModal.tsx`.
- **Existing cards** — operators who already have a `nexplane_agent` connector in their org will still see it (since it's a real DB record), but cannot create new ones. This is acceptable; they can delete it manually.
- **README** — add a clarifying note to the agent section that no connector setup is needed.

## Files Changed

| File | Change |
|------|--------|
| `backend/seed.py` | Remove the `nexplane_agent` connector creation |
| `frontend/src/components/AddConnectorModal.tsx` | Filter `nexplane_agent` out of the connector type options |
| `README.md` | Add note in Agent Configuration section clarifying the secret is the only setup needed |
