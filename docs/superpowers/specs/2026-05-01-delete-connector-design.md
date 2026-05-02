# Delete Connector — Design Spec

**Date:** 2026-05-01
**Status:** Approved
**Scope:** Delete button per connector card with inline two-state confirmation. Adds `DELETE /connectors/{id}` backend endpoint and `connectorsApi.delete` frontend method.

---

## Design Decisions

- **Inline confirmation (no modal):** Confirmation lives on the card itself. The delete button changes to "Confirm delete" (red) + "Cancel" when clicked. No extra component needed.
- **Per-connector confirmation state:** A single `deletingId: string | null` state in `Connectors` tracks which card (if any) is in confirmation state. Only one card can be in confirmation state at a time.
- **Cascade handled by DB:** `ConnectorCredential` and `ScheduledIngest` both have `ondelete='CASCADE'` FKs — no extra cleanup code needed.
- **Simple confirmation copy:** "Delete this connector?" with no cascade warning (user chose option A).
- **Audit event:** Backend records `"connector.deleted"` on success, matching the existing `"connector.created"` pattern.

---

## Backend: `DELETE /connectors/{id}`

**File:** `backend/app/routers/connectors.py`

```python
@router.delete("/{connector_id}", status_code=204)
async def delete_connector(
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_operator),
):
    result = await db.execute(
        select(Connector).where(
            Connector.id == connector_id,
            Connector.organization_id == current_user.organization_id,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")
    await db.delete(connector)
    await db.commit()
    # Audit event (match existing pattern from POST /connectors)
    await audit_service.record(db, current_user, "connector.deleted", {"connector_id": str(connector_id)})
```

Returns 204 No Content on success. 404 if not found or belongs to another org.

---

## Frontend: `connectorsApi.delete`

**File:** `frontend/src/api/endpoints.ts`

Add to the `connectorsApi` object:
```typescript
delete: (id: string) => apiClient.delete(`/connectors/${id}`),
```

---

## Frontend: Delete button + inline confirmation in `Connectors.tsx`

**State added:**
```tsx
const [deletingId, setDeletingId] = useState<string | null>(null);
```

**Delete mutation:**
```tsx
const deleteMutation = useMutation({
  mutationFn: (id: string) => connectorsApi.delete(id),
  onSuccess: (_, id) => {
    setDeletingId(null);
    qc.invalidateQueries({ queryKey: ["connectors"] });
  },
});
```

**In each connector card**, inside the top-right area alongside the status badge, add:

```tsx
{deletingId === connector.id ? (
  <div className="flex items-center gap-2">
    <button
      onClick={() => deleteMutation.mutate(connector.id)}
      disabled={deleteMutation.isPending}
      className="text-xs text-red-600 font-medium hover:text-red-800 disabled:opacity-50"
    >
      {deleteMutation.isPending ? "Deleting…" : "Confirm delete"}
    </button>
    <button
      onClick={() => setDeletingId(null)}
      className="text-xs text-slate-400 hover:text-slate-600"
    >
      Cancel
    </button>
  </div>
) : (
  <button
    onClick={() => setDeletingId(connector.id)}
    className="text-slate-300 hover:text-red-400 transition-colors"
    title="Delete connector"
  >
    <Trash2 className="w-4 h-4" />
  </button>
)}
```

`Trash2` imported from `lucide-react` (already a dependency).

The status badge moves to accommodate — the top-right area of the card becomes a flex row: `[status badge] [delete button/confirmation]`.

---

## Files Changed

| File | Change |
|------|--------|
| `backend/app/routers/connectors.py` | Add `DELETE /{connector_id}` endpoint |
| `frontend/src/api/endpoints.ts` | Add `connectorsApi.delete` |
| `frontend/src/pages/Connectors.tsx` | Add `deletingId` state, `deleteMutation`, delete button + inline confirmation per card |
