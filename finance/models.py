from django.db import models
from core.models import CompanyModel, MONEY, PERCENT
from crm.models import Customer
from operations.models import Order

class FinancialAccount(CompanyModel):
    name = models.CharField(max_length=100)
    bank = models.CharField(max_length=100, blank=True)

class PaymentMethod(CompanyModel):
    name = models.CharField(max_length=100)
    installments = models.PositiveIntegerField(default=1)
    fee_percent = models.DecimalField(**PERCENT)

class FinancialEntry(CompanyModel):
    TYPES = [('income','Entrada'),('expense','Saída')]
    STATUS = [('pending','Pendente'),('settled','Liquidado'),('cancelled','Cancelado'),('reversed','Estornado')]
    type = models.CharField(max_length=8, choices=TYPES)
    account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT)
    description = models.CharField(max_length=220)
    category = models.CharField(max_length=100)
    amount = models.DecimalField(**MONEY)
    due_date = models.DateField()
    settled_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='pending')
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT)
    supplier = models.CharField(max_length=160, blank=True)
    payment_method = models.ForeignKey(PaymentMethod, null=True, blank=True, on_delete=models.PROTECT)
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT)
    installment = models.PositiveIntegerField(default=1)
    installment_count = models.PositiveIntegerField(default=1)
    source_type = models.CharField(max_length=50, blank=True)
    source_id = models.CharField(max_length=40, blank=True)
    idempotency_key = models.CharField(max_length=100, unique=True)
    fee_snapshot = models.JSONField(default=dict)
    reversal_of = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT)
    notes = models.TextField(blank=True)

class Sale(CompanyModel):
    code = models.CharField(max_length=12)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    total = models.DecimalField(**MONEY)
    cost = models.DecimalField(**MONEY)
    fee = models.DecimalField(**MONEY)
    snapshot = models.JSONField(default=dict)
    cancelled_at = models.DateTimeField(null=True, blank=True)

class SaleItem(CompanyModel):
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name='items')
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(**MONEY)
    unit_cost = models.DecimalField(**MONEY)
