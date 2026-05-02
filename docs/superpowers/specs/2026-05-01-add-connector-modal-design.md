# Add Connector Modal — Design Spec

**Date:** 2026-05-01
**Status:** Approved
**Scope:** "Add Connector" button on the Connectors page opens a modal to create a new connector, then flows directly into the existing CredentialModal. No backend changes — `POST /connectors` already exists.

---

## Design Decisions

- **No backend changes:** `POST /connectors` accepts `{connector_type, name, scoped_permissions: {}}` and returns the created connector. Operator role already permitted.
- **Reuse existing CredentialModal:** After creation, the `onCreated` callback sets `credModalConnector` to the new connector — the same state that already drives `CredentialModal` on the page.
- **Auto-fill name from type:** When the operator changes the connector type dropdown, the name field is pre-filled with the friendly label (e.g. selecting `aws_mock` fills "AWS"). Operator can override it.
- **Reuse existing label/icon maps:** `CONNECTOR_LABELS` and `CONNECTOR_ICONS` already defined in `Connectors.tsx` — use them in the modal dropdown.

---

## Component: `AddConnectorModal`

**File:** `frontend/src/components/AddConnectorModal.tsx`

**Props:**
```typescript
interface Props {
  token: string;
  onClose: () => void;
  onCreated: (connector: ConnectorRead) => void;
}
```

**Fields:**
- **Type** — `<select>` dropdown listing all 10 connector types. Each option shows the icon + friendly label from `CONNECTOR_LABELS`. Default: first option (`aws_mock`).
- **Name** — `<input type="text">`. Auto-filled when type changes (set to `CONNECTOR_LABELS[type]`). Operator can edit freely. Required; validated non-empty on submit.

**Behaviour:**
1. Operator selects type → name auto-fills
2. Operator optionally edits name → clicks **Add Connector**
3. `POST /connectors` with `{ connector_type: type, name, scoped_permissions: {} }`
4. On success: call `onCreated(newConnector)`, call `onClose()`
5. On error: show error message inline below the form; do not close modal

**Submit button states:** idle → "Adding…" (disabled while pending) → resets on error.

---

## Changes to `Connectors.tsx`

1. **Import** `AddConnectorModal` and add two state variables:
   - `addModalOpen: boolean` — controls whether `AddConnectorModal` is shown
   - The existing `credModalConnector: ConnectorRead | null` already handles the credential handoff

2. **Header button** — add an **"+ Add Connector"** button (indigo, small) to the top-right of the page header, alongside or near the existing page title. Clicking sets `addModalOpen = true`.

3. **Render `AddConnectorModal`** (conditionally):
   ```tsx
   {addModalOpen && (
     <AddConnectorModal
       token={token}
       onClose={() => setAddModalOpen(false)}
       onCreated={(connector) => {
         setAddModalOpen(false);
         queryClient.invalidateQueries({ queryKey: ['connectors'] });
         setCredModalConnector(connector);  // opens CredentialModal
       }}
     />
   )}
   ```

4. **No other changes** — `CredentialModal` render and all existing logic is unchanged.

---

## Files

| File | Change |
|------|--------|
| `frontend/src/components/AddConnectorModal.tsx` | New — the modal component |
| `frontend/src/pages/Connectors.tsx` | Add button, `addModalOpen` state, `AddConnectorModal` render |
