# smartprocureapp/po_request_views.py
"""
Purchase Order Request workflow

    Stores raise  ->  Procurement add rates  ->  GM approve
    ->  Director approve  ->  Finance

Each role has one page. All actions are JSON POSTs to the page's own URL:
    {"action": "create" | "price" | "approve" | "reject" | "process", ...}
"""
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import pytz
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from . import notifications as notif
from .models import ApprovalLog, Material, PORequest, PORequestItem, Vendor
from .views import RoleRequiredMixin, generate_sequential_id, log_user_action

IST = pytz.timezone("Asia/Kolkata")
TWO = Decimal("0.01")
MAX_DEC = Decimal("9999999.99")      # fits max_digits=12, decimal_places=2
MAX_ITEMS = 100
PO_PREFIX = "POR-"


class POError(Exception):
    """Raised for any validation / permission problem; message is shown in the UI."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _fmt_dt(dt):
    return timezone.localtime(dt, IST).strftime("%d-%m-%Y %I:%M %p") if dt else ""


def _plain(d):
    """Decimal -> '10' / '2.5' (no exponent, no trailing zeros)."""
    if d is None:
        return ""
    return format(d.normalize(), "f")


def _money(d):
    return "" if d is None else f"{d:.2f}"


def _dec(value, label):
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise POError(f"{label} is not a valid number.")
    if not d.is_finite():
        raise POError(f"{label} is not a valid number.")
    d = d.quantize(TWO, rounding=ROUND_HALF_UP)
    if d <= 0:
        raise POError(f"{label} must be greater than zero.")
    if d > MAX_DEC:
        raise POError(f"{label} is too large.")
    return d


def _text(value, label, maxlen=255, required=False, minlen=0):
    s = (value or "").strip() if isinstance(value, str) or value is None else str(value).strip()
    if required and len(s) < max(1, minlen):
        raise POError(f"{label} is required" + (f" (at least {minlen} characters)." if minlen > 1 else "."))
    if len(s) > maxlen:
        raise POError(f"{label} can have at most {maxlen} characters.")
    return s


def _payload(request):
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise POError("Invalid request.")
    if not isinstance(data, dict):
        raise POError("Invalid request.")
    return data


def _queryset():
    return (
        PORequest.objects
        .select_related("vendor", "created_by", "procurement_by", "gm_by", "director_by", "finance_by", "rejected_by")
        .prefetch_related("items__material")
        .order_by("-id")
    )


def serialize_po(po):
    items = [
        {
            "id": it.id,
            "material": it.material.name,
            "unit": it.material.unit,
            "quantity": _plain(it.quantity),
            "unit_price": _money(it.unit_price),
            "amount": _money(it.amount),
        }
        for it in po.items.all()
    ]

    def who(u):
        return u.username if u else ""

    trail = [{"stage": "Raised by Stores", "by": who(po.created_by), "at": _fmt_dt(po.created_at),
              "remarks": po.stores_remarks, "state": "done"}]
    if po.procurement_at:
        trail.append({"stage": "Rates added by Procurement", "by": who(po.procurement_by),
                      "at": _fmt_dt(po.procurement_at), "remarks": po.procurement_remarks, "state": "done"})
    if po.gm_at:
        trail.append({"stage": "Approved by GM", "by": who(po.gm_by), "at": _fmt_dt(po.gm_at),
                      "remarks": po.gm_remarks, "state": "done"})
    if po.director_at:
        trail.append({"stage": "Approved by Director", "by": who(po.director_by), "at": _fmt_dt(po.director_at),
                      "remarks": po.director_remarks, "state": "done"})
    if po.finance_at:
        trail.append({"stage": "Processed by Finance", "by": who(po.finance_by), "at": _fmt_dt(po.finance_at),
                      "remarks": po.finance_remarks, "state": "done"})
    if po.status == PORequest.STATUS_REJECTED:
        trail.append({"stage": f"Rejected by {po.rejected_stage}", "by": who(po.rejected_by),
                      "at": _fmt_dt(po.rejected_at), "remarks": po.rejected_reason, "state": "bad"})

    return {
        "id": po.id,
        "po_number": po.po_number,
        "vendor": po.vendor.name,
        "status": po.status,
        "created_by": who(po.created_by),
        "created_at": _fmt_dt(po.created_at),
        "item_count": len(items),
        "items": items,
        "total_amount": _money(po.total_amount),
        "trail": trail,
        "rejected_reason": po.rejected_reason,
    }


def _locked(pk):
    po = PORequest.objects.select_for_update().filter(pk=pk).first()
    if not po:
        raise POError("PO request not found.", 404)
    return po


def _audit(request, po, action, remarks=""):
    ApprovalLog.objects.create(
        user=request.user, action=action, object_type="PORequest", object_id=po.id, remarks=remarks or "",
    )
    log_user_action(
        request.user.username,
        f"{action} PO Request {po.po_number} ({request.user.profile.role})",
    )


class _BasePOView(RoleRequiredMixin, View):
    """Shared dispatch: JSON POST -> self.handle(action, payload); POError -> JSON error."""
    handlers = {}

    def post(self, request):
        try:
            payload = _payload(request)
            action = payload.get("action")
            handler = self.handlers.get(action)
            if not handler:
                raise POError("Unknown action.")
            result = getattr(self, handler)(request, payload)
            return JsonResponse({"success": True, **result})
        except POError as exc:
            return JsonResponse({"error": str(exc)}, status=exc.status)

    def page(self, request, template, qs, extra=None):
        ctx = {"po_data": [serialize_po(p) for p in qs], "role": request.user.profile.role}
        ctx.update(extra or {})
        return render(request, template, ctx)


# ---------------------------------------------------------------------------
# 1. STORES  - create the request
# ---------------------------------------------------------------------------
class POStoresView(_BasePOView):
    required_roles = ["Stores"]
    handlers = {"create": "create"}

    def get(self, request):
        vendors = [{"id": v.id, "name": v.name} for v in Vendor.objects.filter(is_active=True).order_by("name")]
        materials = [
            {"id": m.id, "name": m.name, "unit": m.unit}
            for m in Material.objects.filter(material_type="raw", is_active=True).order_by("name")
        ]
        return self.page(request, "po_requests/stores_page.html", _queryset(), {
            "vendors": vendors,
            "materials": materials,
            "next_po_number": generate_sequential_id(PORequest, "po_number", PO_PREFIX),
        })

    def create(self, request, payload):
        vendor = Vendor.objects.filter(pk=payload.get("vendor_id"), is_active=True).first() \
            if str(payload.get("vendor_id") or "").isdigit() else None
        if not vendor:
            raise POError("Please select a vendor.")

        rows = payload.get("items")
        if not isinstance(rows, list) or not rows:
            raise POError("Add at least one raw material.")
        if len(rows) > MAX_ITEMS:
            raise POError(f"A request can have at most {MAX_ITEMS} materials.")

        remarks = _text(payload.get("remarks"), "Remarks")

        clean, seen = [], set()
        for n, row in enumerate(rows, 1):
            row = row if isinstance(row, dict) else {}
            mid = str(row.get("material_id") or "")
            material = Material.objects.filter(pk=mid, material_type="raw", is_active=True).first() if mid.isdigit() else None
            if not material:
                raise POError(f"Row {n}: please select a raw material.")
            if material.id in seen:
                raise POError(f"Row {n}: {material.name} is added more than once.")
            seen.add(material.id)
            clean.append((material, _dec(row.get("quantity"), f"Row {n}: quantity")))

        po = None
        for _ in range(5):                       # retry if two users get the same number at once
            try:
                with transaction.atomic():
                    po = PORequest.objects.create(
                        po_number=generate_sequential_id(PORequest, "po_number", PO_PREFIX),
                        vendor=vendor, created_by=request.user, stores_remarks=remarks,
                    )
                    PORequestItem.objects.bulk_create(
                        [PORequestItem(po_request=po, material=m, quantity=q) for m, q in clean]
                    )
                break
            except IntegrityError:
                po = None
        if po is None:
            raise POError("Could not generate a PO number. Please try again.", 409)

        _audit(request, po, "Created", remarks)
        notif.notify_po_submitted(po)
        return {"po_number": po.po_number, "message": f"PO request {po.po_number} sent to Procurement."}


# ---------------------------------------------------------------------------
# 2. PROCUREMENT - add rates / amount
# ---------------------------------------------------------------------------
class POProcurementView(_BasePOView):
    required_roles = ["Procurement"]
    handlers = {"price": "price"}

    def get(self, request):
        return self.page(request, "po_requests/procurement_page.html", _queryset())

    @transaction.atomic
    def price(self, request, payload):
        po = _locked(payload.get("id"))
        if po.status != PORequest.STATUS_PROCUREMENT:
            raise POError("This request is no longer waiting for rates.", 409)

        rows = payload.get("items")
        if not isinstance(rows, list):
            raise POError("Enter a rate for every material.")
        prices = {}
        for row in rows:
            row = row if isinstance(row, dict) else {}
            if str(row.get("item_id") or "").isdigit():
                prices[int(row["item_id"])] = row.get("unit_price")

        remarks = _text(payload.get("remarks"), "Remarks")
        total = Decimal("0.00")
        for it in po.items.select_related("material"):
            if it.id not in prices:
                raise POError(f"Enter a rate for {it.material.name}.")
            rate = _dec(prices[it.id], f"Rate for {it.material.name}")
            it.unit_price = rate
            it.amount = (it.quantity * rate).quantize(TWO, rounding=ROUND_HALF_UP)
            it.save(update_fields=["unit_price", "amount"])
            total += it.amount
        if total > Decimal("99999999999.99"):
            raise POError("Total amount is too large.")

        po.total_amount = total
        po.procurement_by = request.user
        po.procurement_at = timezone.now()
        po.procurement_remarks = remarks
        po.status = PORequest.STATUS_GM
        po.save()

        _audit(request, po, "Added rates to", remarks)
        notif.notify_po_priced(po)
        return {"message": f"{po.po_number} sent to GM for approval."}


# ---------------------------------------------------------------------------
# 3. GM / DIRECTOR - approve or reject
# ---------------------------------------------------------------------------
class POApprovalView(_BasePOView):
    required_roles = ["GM", "Director"]
    handlers = {"approve": "approve", "reject": "reject"}

    # which status each role is allowed to decide on
    STAGE = {"GM": PORequest.STATUS_GM, "Director": PORequest.STATUS_DIRECTOR}

    def get(self, request):
        role = request.user.profile.role
        qs = _queryset().exclude(status=PORequest.STATUS_PROCUREMENT)
        return self.page(request, "po_requests/approvals_page.html", qs, {"pending_status": self.STAGE[role]})

    def _lock_for(self, request, payload):
        role = request.user.profile.role
        po = _locked(payload.get("id"))
        if po.status != self.STAGE[role]:
            raise POError("This request is not waiting for your approval.", 409)
        return role, po

    @transaction.atomic
    def approve(self, request, payload):
        role, po = self._lock_for(request, payload)
        remarks = _text(payload.get("remarks"), "Remarks")
        now = timezone.now()
        if role == "GM":
            po.gm_by, po.gm_at, po.gm_remarks = request.user, now, remarks
            po.status = PORequest.STATUS_DIRECTOR
        else:
            po.director_by, po.director_at, po.director_remarks = request.user, now, remarks
            po.status = PORequest.STATUS_FINANCE
        po.save()

        _audit(request, po, "Approved", remarks)
        if role == "GM":
            notif.notify_po_gm_approved(po)
            message = f"{po.po_number} approved and sent to Director."
        else:
            notif.notify_po_director_approved(po)
            message = f"{po.po_number} approved and sent to Finance."
        return {"message": message}

    @transaction.atomic
    def reject(self, request, payload):
        role, po = self._lock_for(request, payload)
        reason = _text(payload.get("remarks"), "Reason", required=True, minlen=3)
        po.status = PORequest.STATUS_REJECTED
        po.rejected_stage, po.rejected_by, po.rejected_at, po.rejected_reason = role, request.user, timezone.now(), reason
        po.save()

        _audit(request, po, "Rejected", reason)
        notif.notify_po_rejected(po)
        return {"message": f"{po.po_number} rejected."}


# ---------------------------------------------------------------------------
# 4. FINANCE
# ---------------------------------------------------------------------------
class POFinanceView(_BasePOView):
    required_roles = ["Finance"]
    handlers = {"process": "process"}

    def get(self, request):
        qs = _queryset().filter(status__in=[PORequest.STATUS_FINANCE, PORequest.STATUS_COMPLETED])
        return self.page(request, "po_requests/finance_page.html", qs)

    @transaction.atomic
    def process(self, request, payload):
        po = _locked(payload.get("id"))
        if po.status != PORequest.STATUS_FINANCE:
            raise POError("This request is not waiting for Finance.", 409)
        remarks = _text(payload.get("remarks"), "Remarks")
        po.finance_by, po.finance_at, po.finance_remarks = request.user, timezone.now(), remarks
        po.status = PORequest.STATUS_COMPLETED
        po.save()

        _audit(request, po, "Processed", remarks)
        notif.notify_po_completed(po)
        return {"message": f"{po.po_number} marked as processed."}
