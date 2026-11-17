# =====================================================================
# PURCHASE ORDER REQUEST WORKFLOW
# Paste this block at the END of smartprocureapp/models.py
#
#   Stores raise  ->  Procurement add rates  ->  GM approve
#   ->  Director approve  ->  Finance
#
# (Separate from the existing PurchaseOrder / PurchaseOrderItem models,
#  which keep working exactly as before.)
# =====================================================================
class PORequest(models.Model):
    STATUS_PROCUREMENT = "Procurement Pending"   # raised by Stores, waiting for Procurement rates
    STATUS_GM = "GM Approval"                    # priced, waiting for GM
    STATUS_DIRECTOR = "Director Approval"        # GM approved, waiting for Director
    STATUS_FINANCE = "Finance Approval"          # Director approved, now with Finance
    STATUS_COMPLETED = "Completed"               # Finance processed it
    STATUS_REJECTED = "Rejected"                 # rejected by GM or Director

    STATUS_CHOICES = [
        (STATUS_PROCUREMENT, STATUS_PROCUREMENT),
        (STATUS_GM, STATUS_GM),
        (STATUS_DIRECTOR, STATUS_DIRECTOR),
        (STATUS_FINANCE, STATUS_FINANCE),
        (STATUS_COMPLETED, STATUS_COMPLETED),
        (STATUS_REJECTED, STATUS_REJECTED),
    ]

    po_number = models.CharField(max_length=50, unique=True)
    vendor = models.ForeignKey('Vendor', on_delete=models.PROTECT, related_name='po_requests')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_PROCUREMENT)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    # Stores (creator)
    stores_remarks = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='po_requests_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Procurement (adds rates)
    procurement_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    procurement_at = models.DateTimeField(null=True, blank=True)
    procurement_remarks = models.CharField(max_length=255, blank=True, default="")

    # GM
    gm_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    gm_at = models.DateTimeField(null=True, blank=True)
    gm_remarks = models.CharField(max_length=255, blank=True, default="")

    # Director
    director_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    director_at = models.DateTimeField(null=True, blank=True)
    director_remarks = models.CharField(max_length=255, blank=True, default="")

    # Finance
    finance_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    finance_at = models.DateTimeField(null=True, blank=True)
    finance_remarks = models.CharField(max_length=255, blank=True, default="")

    # Rejection (GM or Director)
    rejected_stage = models.CharField(max_length=20, blank=True, default="")   # "GM" / "Director"
    rejected_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return self.po_number


class PORequestItem(models.Model):
    po_request = models.ForeignKey(PORequest, on_delete=models.CASCADE, related_name='items')
    material = models.ForeignKey('Material', on_delete=models.PROTECT, related_name='po_request_items')
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)   # added by Procurement
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)       # quantity x unit_price

    class Meta:
        unique_together = ("po_request", "material")

    def __str__(self):
        return f"{self.po_request.po_number} - {self.material.name}"
