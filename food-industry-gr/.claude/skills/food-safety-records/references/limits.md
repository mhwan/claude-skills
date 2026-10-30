# Limits of this skill

What the skill does not do, so the answer does not promise more than the table holds.

## Scope
- Only the three lists: recalls and sales stops, administrative actions, failed inspections. Nothing else on the portal (product search, notices, statistics) is read.
- The portal publishes these records after the event. A recall that happened today may not be listed yet. "Not seen" never proves "did not happen".
- The skill does not judge risk, severity or whether a product is still on shelves. It does not tell consumers what to do. For anything beyond repeating the list, point to the detail link.
- It does not match products to the user's own stock. ★ only means the company name or product text matched an entry of the tracked list.

## Period
- Default: the last 14 days ending today, by the computer clock.
- Rows are kept when their basis date is inside the period. The basis date differs by kind (see the portal notes).
- A long period means many pages. The script stops at 30 pages per kind; if it stops there the report says the table is partial. Narrow the period or run it twice with adjoining periods.
- A future end date is refused.

## Repeating a run
- Each run writes a new run folder with the request log (method, address, a short note; never response bodies).
- A report with the same name moves to `history/` before the new one is written. When nothing could be read, the old report stays.
- Carrying rows from one report to the next (new / still open) is not done here.

## Recorded inputs
`run --replay DIR` answers every request from recorded files instead of the portal and marks the result with a warning. It exists for tests. A report made that way is not a statement about the real portal and must not be shown as one.

## Not covered
- No browser, no screenshots of the portal.
- No Windows app run has been seen; Windows behaviour is checked only by path and console-encoding tests ("실제 Windows 앱 미확인").
