# smartprocureapp/notifications.py
from django.urls import reverse, NoReverseMatch

from .models import Notification


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_url(name, query="", kwargs=None):
    """reverse() that never raises – returns '#' if the URL name is missing."""
    try:
        return f"{reverse(name, kwargs=kwargs)}{query}"
    except NoReverseMatch:
        return "#"


def notify_roles(roles, *, category, title, message, link="#",
                 object_type="", object_id=None):
    """
    Create one unread notification per role, de-duplicated on
    (role, object_type, object_id, title) while the old one is still unread.
    """
    created = []
    for role in roles:
        if not role:
            continue

        exists = Notification.objects.filter(
            role=role,
            object_type=object_type,
            object_id=object_id,
            title=title,
            is_read=False,
        ).exists()

        if exists:
            continue

        created.append(
            Notification(
                role=role,
                category=category,
                title=title,
                message=message,
                link=link,
                object_type=object_type,
                object_id=object_id,
            )
        )

    if created:
        Notification.objects.bulk_create(created)
    return created


def notify_user(user_profile, *, category, title, message, link="#",
                object_type="", object_id=None):
    """
    Send a notification to ONE specific user (UserProfile),
    de-duplicated the same way as notify_roles.
    """
    if not user_profile:
        return None

    exists = Notification.objects.filter(
        user_profile=user_profile,
        object_type=object_type,
        object_id=object_id,
        title=title,
        is_read=False,
    ).exists()
    if exists:
        return None

    return Notification.objects.create(
        user_profile=user_profile,
        category=category,
        title=title,
        message=message,
        link=link,
        object_type=object_type,
        object_id=object_id,
    )


# ---------------------------------------------------------------------------
# Sales / Sample issues  (already used in views.py)
# ---------------------------------------------------------------------------
def notify_sales_sample_issue(issue):
    """Route a SalesSampleIssue notification to whoever must act next."""
    status = (issue.status or "").strip()

    routing = {
        "Pending":            ["Stores"],
        "GM Approval":        ["GM", "Director"],
        "Finance Approval":   ["Finance"],
        "Approved":           ["Stores"],
        "Ready for Dispatch": ["Stores"],
    }
    roles = routing.get(status)
    if not roles:
        return

    customer = ""
    if issue.order and issue.order.customer:
        customer = issue.order.customer.name

    link = _safe_url("finance_orders", f"?type={issue.type}")

    notify_roles(
        roles,
        category="ORDER",
        title=f"{issue.type} Request – {status}",
        message=(
            f"{issue.type} request {issue.issue_id} "
            f"{'for ' + customer + ' ' if customer else ''}"
            f"is awaiting your action ({status})."
        ),
        link=link,
        object_type="SalesSampleIssue",
        object_id=issue.id,
    )


# ---------------------------------------------------------------------------
# Stores <-> Production events
# ---------------------------------------------------------------------------
def _other(role):
    """The opposite department."""
    return "Production" if role == "Stores" else "Stores"


# ---- Material Request -----------------------------------------------------
def notify_material_request_created(material_request, creator_role):
    """Production raised an MR -> Stores acts. Stores raised it -> Production."""
    notify_roles(
        [_other(creator_role)],
        category="PRODUCTION",
        title="New Material Request",
        message=(
            f"Material request {material_request.mr_number} raised by "
            f"{creator_role} ({material_request.urgency} urgency) needs attention."
        ),
        link=_safe_url("material_request_detail", kwargs={"pk": material_request.id}),
        object_type="MaterialRequest",
        object_id=material_request.id,
    )


def notify_issue_slip_created(issue_slip):
    """Stores created an issue slip -> Production must accept it."""
    mr = issue_slip.mr_number            # FK to MaterialRequest
    notify_roles(
        ["Production"],
        category="PRODUCTION",
        title="Issue Slip Created",
        message=(
            f"Issue slip {issue_slip.issue_number} for material request "
            f"{mr.mr_number} is waiting for your acceptance."
        ),
        link=_safe_url("material_request_detail", kwargs={"pk": mr.id}),
        object_type="IssueSlip",
        object_id=issue_slip.id,
    )


def notify_issue_slip_status(issue_slip, status, actor_role):
    """Issue slip accepted / rejected / cancelled -> tell the other department."""
    mr = issue_slip.mr_number
    notify_roles(
        [_other(actor_role)],
        category="PRODUCTION",
        title=f"Issue Slip {status}",
        message=(
            f"Issue slip {issue_slip.issue_number} for {mr.mr_number} "
            f"was {status.lower()} by {actor_role}."
        ),
        link=_safe_url("material_request_detail", kwargs={"pk": mr.id}),
        object_type="IssueSlip",
        object_id=issue_slip.id,
    )


# ---- Production Request ---------------------------------------------------
def notify_production_request_created(production_request, source=""):
    """A Production Request was raised -> Production must make the indent."""
    via = f" ({source})" if source else ""
    notify_roles(
        ["Production"],
        category="PRODUCTION",
        title="New Production Request",
        message=f"Production request {production_request.pr_number} has been raised{via}.",
        link=_safe_url("production_request_detail", kwargs={"pk": production_request.pk}),
        object_type="ProductionRequest",
        object_id=production_request.pk,
    )


def notify_indent_created(indent):
    """Production created an indent (FG to Stores) -> Stores must accept."""
    pr = getattr(indent, "production_request", None)
    pr_text = f" for production request {pr.pr_number}" if pr else ""
    notify_roles(
        ["Stores"],
        category="PRODUCTION",
        title="New Indent",
        message=f"Indent {indent.indent_number}{pr_text} is waiting for your acceptance.",
        link=_safe_url("production_request_detail", kwargs={"pk": pr.pk}) if pr
        else _safe_url("issue_history"),
        object_type="Indent",
        object_id=indent.pk,
    )


def notify_indent_status(indent, status, actor_role):
    """Indent accepted / rejected / cancelled -> tell the other department."""
    pr = getattr(indent, "production_request", None)
    notify_roles(
        [_other(actor_role)],
        category="PRODUCTION",
        title=f"Indent {status}",
        message=f"Indent {indent.indent_number} was {status.lower()} by {actor_role}.",
        link=_safe_url("production_request_detail", kwargs={"pk": pr.pk}) if pr
        else _safe_url("issue_history"),
        object_type="Indent",
        object_id=indent.pk,
    )


# ---- Return / Damage ------------------------------------------------------
def notify_return_or_damage(entry):
    """A Return / Damage GoodsEntry was created -> whoever approves next."""
    stage = (entry.current_stage or "").strip() or "Stores"
    roles = ["GM", "Director"] if stage == "GM" else [stage]
    category = "DAMAGE" if entry.type == "Damage" else "RETURN"

    notify_roles(
        roles,
        category=category,
        title=f"{entry.type} Pending Approval",
        message=f"{entry.type} entry {entry.formatted_id} is awaiting your approval.",
        link=_safe_url("gmapprovals", f"?type={entry.type}") if stage == "GM"
        else _safe_url("return_damage"),
        object_type="GoodsEntry",
        object_id=entry.id,
    )


def notify_entry_result(entry, status, actor_role):
    """Return/Damage approved, rejected or accepted -> tell whoever created it."""
    creator = getattr(entry.created_by, "profile", None) if entry.created_by_id else None
    category = "DAMAGE" if entry.type == "Damage" else "RETURN"
    title = f"{entry.type} {status}"
    message = f"{entry.type} entry {entry.formatted_id} was {status.lower()} by {actor_role}."
    link = _safe_url("return_damage")

    if creator:
        notify_user(creator, category=category, title=title, message=message,
                    link=link, object_type="GoodsEntry", object_id=entry.id)


def notify_adjustment_result(adjustment_request, status):
    """Adjustment approved / rejected by GM -> tell the person who requested it."""
    creator = getattr(adjustment_request.created_by, "profile", None) \
        if adjustment_request.created_by_id else None
    if not creator:
        return
    notify_user(
        creator,
        category="ADJUSTMENT",
        title=f"Adjustment {status}",
        message=f"Your adjustment request {adjustment_request.request_id} was {status.lower()}.",
        link="#",
        object_type="AdjustmentRequest",
        object_id=adjustment_request.id,
    )


def notify_pr_status(production_request, status, actor_role):
    """Production Request cancelled / started / completed -> tell the other department."""
    notify_roles(
        [_other(actor_role)],
        category="PRODUCTION",
        title=f"Production Request {status}",
        message=f"Production request {production_request.pr_number} was {status.lower()} by {actor_role}.",
        link=_safe_url("production_request_detail", kwargs={"pk": production_request.pk}),
        object_type="ProductionRequest",
        object_id=production_request.pk,
    )


# ---- Material Request: comments, cancel, completed -----------------------
def _counterparts(actor_role):
    """Stores <-> Production. Any other role (GM, Procurement...) tells both."""
    if actor_role in ("Stores", "Production"):
        return [_other(actor_role)]
    return ["Stores", "Production"]


def notify_material_request_comment(material_request, comment, actor_role):
    """Someone replied on a Material Request -> the other department is told."""
    text = (comment.comment or "").strip()
    snippet = text if len(text) <= 80 else text[:77] + "..."
    notify_roles(
        _counterparts(actor_role),
        category="PRODUCTION",
        title=f"New comment on {material_request.mr_number}",
        message=f"{actor_role}: {snippet}",
        link=_safe_url("material_request_detail", kwargs={"pk": material_request.id}),
        object_type="MaterialRequestComment",
        object_id=comment.id,
    )


def notify_material_request_cancelled(material_request, actor_role):
    """A Material Request was cancelled -> the other department is told."""
    notify_roles(
        _counterparts(actor_role),
        category="PRODUCTION",
        title="Material Request Cancelled",
        message=(
            f"Material request {material_request.mr_number} was cancelled by "
            f"{actor_role}. Its pending issue slips were cancelled too."
        ),
        link=_safe_url("material_request_detail", kwargs={"pk": material_request.id}),
        object_type="MaterialRequest",
        object_id=material_request.id,
    )


def notify_material_request_completed(material_request):
    """Every item has been issued and accepted -> Stores can close it out."""
    notify_roles(
        ["Stores"],
        category="PRODUCTION",
        title="Material Request Completed",
        message=f"Material request {material_request.mr_number} is now fully completed.",
        link=_safe_url("material_request_detail", kwargs={"pk": material_request.id}),
        object_type="MaterialRequest",
        object_id=material_request.id,
    )


# ---- Purchase Order Requests (Stores -> Procurement -> GM -> Director -> Finance) ----
def _po_profile(po):
    return getattr(po.created_by, "profile", None) if po.created_by_id else None


def _po_money(po):
    return f"₹{po.total_amount:,.2f}" if po.total_amount is not None else ""


def _po_notify(po, roles, title, message, page):
    notify_roles(
        roles,
        category="GENERAL",
        title=title,
        message=message,
        link=_safe_url(page, f"?po={po.po_number}"),
        object_type="PORequest",
        object_id=po.id,
    )


def notify_po_submitted(po):
    """Stores raised a PO request -> Procurement must add the rates."""
    _po_notify(po, ["Procurement"], "New PO Request",
               f"PO request {po.po_number} for {po.vendor.name} was raised by Stores. Please add the rates.",
               "po_request_procurement")


def notify_po_priced(po):
    """Procurement added the rates -> GM approves."""
    _po_notify(po, ["GM"], "PO Request – GM Approval",
               f"PO request {po.po_number} ({po.vendor.name}, {_po_money(po)}) is awaiting your approval.",
               "po_request_approvals")


def notify_po_gm_approved(po):
    """GM approved -> Director approves."""
    _po_notify(po, ["Director"], "PO Request – Director Approval",
               f"PO request {po.po_number} ({po.vendor.name}, {_po_money(po)}) was approved by GM and awaits your approval.",
               "po_request_approvals")


def notify_po_director_approved(po):
    """Director approved -> Finance acts; Stores is told it was approved."""
    _po_notify(po, ["Finance"], "PO Request – Finance",
               f"PO request {po.po_number} ({po.vendor.name}, {_po_money(po)}) is approved and ready for Finance.",
               "po_request_finance")
    profile = _po_profile(po)
    if profile:
        notify_user(profile, category="GENERAL", title="PO Request Approved",
                    message=f"Your PO request {po.po_number} was approved by GM and Director and sent to Finance.",
                    link=_safe_url("po_request_stores", f"?po={po.po_number}"),
                    object_type="PORequest", object_id=po.id)


def notify_po_rejected(po):
    """Rejected by GM / Director -> tell the Stores creator and Procurement."""
    reason = f" Reason: {po.rejected_reason}" if po.rejected_reason else ""
    message = f"PO request {po.po_number} was rejected by {po.rejected_stage}.{reason}"
    profile = _po_profile(po)
    if profile:
        notify_user(profile, category="GENERAL", title="PO Request Rejected", message=message,
                    link=_safe_url("po_request_stores", f"?po={po.po_number}"),
                    object_type="PORequest", object_id=po.id)
    roles = ["Procurement"] + (["GM"] if po.rejected_stage == "Director" else [])
    _po_notify(po, roles, "PO Request Rejected", message, "po_request_procurement")


def notify_po_completed(po):
    """Finance processed it -> tell the Stores creator and Procurement."""
    message = f"PO request {po.po_number} ({po.vendor.name}, {_po_money(po)}) was processed by Finance."
    profile = _po_profile(po)
    if profile:
        notify_user(profile, category="GENERAL", title="PO Request Processed", message=message,
                    link=_safe_url("po_request_stores", f"?po={po.po_number}"),
                    object_type="PORequest", object_id=po.id)
    _po_notify(po, ["Procurement"], "PO Request Processed", message, "po_request_procurement")

