# smartprocureapp/urls.py  --  add these lines

# 1) at the top, with the other imports:
from . import po_request_views

# 2) inside urlpatterns = [ ... ]:
    path("po-requests/stores/",      po_request_views.POStoresView.as_view(),      name="po_request_stores"),
    path("po-requests/procurement/", po_request_views.POProcurementView.as_view(), name="po_request_procurement"),
    path("po-requests/approvals/",   po_request_views.POApprovalView.as_view(),    name="po_request_approvals"),
    path("po-requests/finance/",     po_request_views.POFinanceView.as_view(),     name="po_request_finance"),
