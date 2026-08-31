from django.db import models
from core.models import CompanyModel, MONEY, QTY, PERCENT
from inventory.models import Product

class ConsignmentStore(CompanyModel):
    name = models.CharField(max_length=180)
    manager = models.CharField(max_length=160, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    default_commission = models.DecimalField(**PERCENT)
    notes = models.TextField(blank=True)

class Shipment(CompanyModel):
    code = models.CharField(max_length=12)
    store = models.ForeignKey(ConsignmentStore, on_delete=models.PROTECT)
    shipped_at = models.DateField()
    snapshot = models.JSONField(default=dict)
    cancelled_at = models.DateTimeField(null=True, blank=True)

class ShipmentItem(CompanyModel):
    shipment = models.ForeignKey(Shipment, on_delete=models.PROTECT, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(**QTY)
    remaining = models.DecimalField(**QTY)
    unit_price = models.DecimalField(**MONEY)
    commission_percent = models.DecimalField(**PERCENT)

class Settlement(CompanyModel):
    code = models.CharField(max_length=12)
    store = models.ForeignKey(ConsignmentStore, on_delete=models.PROTECT)
    period_start = models.DateField()
    period_end = models.DateField()
    settled_on = models.DateField()
    units_sold = models.DecimalField(**QTY)
    gross = models.DecimalField(**MONEY)
    commission = models.DecimalField(**MONEY)
    net = models.DecimalField(**MONEY)
    completed_at = models.DateTimeField(null=True, blank=True)
    snapshot = models.JSONField(default=dict)

class SettlementItem(CompanyModel):
    settlement = models.ForeignKey(Settlement, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    expected = models.DecimalField(**QTY)
    found = models.DecimalField(max_digits=14, decimal_places=3, null=True)
    sold = models.DecimalField(**QTY)
    unit_price = models.DecimalField(**MONEY)
    commission_percent = models.DecimalField(**PERCENT)
    gross = models.DecimalField(**MONEY)
    net = models.DecimalField(**MONEY)
    divergence_resolved = models.BooleanField(default=False)
