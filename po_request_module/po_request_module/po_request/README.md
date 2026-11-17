# Purchase Order Request workflow

Stores raise  ->  Procurement add rates  ->  GM approve  ->  Director approve  ->  Finance

## Install (5 steps)

1. **models.py** – paste the whole of `models_append.py` at the END of `smartprocureapp/models.py`.
2. **Views** – copy `po_request_views.py` into `smartprocureapp/` (next to views.py). Your big views.py is not touched.
3. **Notifications** – replace `smartprocureapp/notifications.py` with the `notifications.py` provided
   (it is your current file plus 6 new `notify_po_*` functions at the bottom).
4. **Templates** – copy the folder `templates/po_requests/` to `smartprocureapp/templates/po_requests/`
   (5 files: `_po_common.html`, `stores_page.html`, `procurement_page.html`, `approvals_page.html`, `finance_page.html`).
5. **URLs + menu** – add the 4 lines from `urls_snippet.py`, and the 4 sidebar links from `sidebar_snippets.html`
   (one per role, in both the desktop and the mobile menu of base.html).

Then:

    python manage.py makemigrations smartprocureapp
    python manage.py migrate

The migration only ADDS two new tables (PORequest, PORequestItem). Nothing existing is changed.

## Who does what

| Role        | Page                  | Can do |
|-------------|-----------------------|--------|
| Stores      | Purchase Requests     | PO number (auto), vendor dropdown, raw materials + quantity -> Submit |
| Procurement | PO Requests           | See the table, "Add Amount" (rate per material, total is calculated) -> goes to GM |
| GM          | PO Approvals          | Approve (-> Director) or Reject with a reason |
| Director    | PO Approvals          | Approve (-> Finance) or Reject with a reason |
| Finance     | PO Requests           | See approved POs, "Mark Processed" |

Rejected at GM or Director = final. Stores and Procurement are notified with the reason.

## Notifications (bell)

New request -> Procurement | Rates added -> GM | GM approved -> Director |
Director approved -> Finance (+ the Stores creator) | Rejected -> creator + Procurement (+ GM if the Director rejected) |
Processed -> creator + Procurement.
Clicking a notification opens the page and the PO detail panel automatically.

NOTE: GM-stage notifications go to role "GM". If you want the Director to see them too, apply the
`Q(role__in=["GM", "Director"])` fix in `unread_notifications` / `mark_notification_read` from earlier.
