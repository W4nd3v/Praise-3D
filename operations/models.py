from django.conf import settings
from django.db import models
from core.models import CompanyModel, MONEY, QTY, PERCENT
from crm.models import Customer
from inventory.models import Material, Printer, CalculationModel, Product

class QuoteRequest(CompanyModel):
    ORIGINS = [('whatsapp','WhatsApp'),('instagram','Instagram'),('in_person','Presencial'),('phone','Telefone'),('referral','Indicação'),('other','Outro')]
    code = models.CharField(max_length=12)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    description = models.CharField(max_length=220)
    notes = models.TextField(blank=True)
    origin = models.CharField(max_length=12, choices=ORIGINS)
    reminder_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default='new')
    class Meta: constraints = [models.UniqueConstraint(fields=['company','code'], name='unique_request_code')]

class Quote(CompanyModel):
    code = models.CharField(max_length=12)
    request = models.OneToOneField(QuoteRequest, on_delete=models.PROTECT)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, default='draft')
    valid_until = models.DateField(null=True)
    discount_percent = models.DecimalField(**PERCENT)
    predicted_cost = models.DecimalField(**MONEY)
    suggested_price = models.DecimalField(**MONEY)
    final_price = models.DecimalField(**MONEY)
    predicted_profit = models.DecimalField(**MONEY)
    cost_snapshot = models.JSONField(default=dict)
    class Meta: constraints = [models.UniqueConstraint(fields=['company','code'], name='unique_quote_code')]

class QuoteItem(CompanyModel):
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name='items')
    description = models.CharField(max_length=220)
    quantity = models.DecimalField(**QTY)
    material = models.ForeignKey(Material, null=True, blank=True, on_delete=models.PROTECT)
    color = models.CharField(max_length=60, blank=True)
    grams = models.DecimalField(**QTY)
    print_minutes = models.PositiveIntegerField(default=0)
    printer = models.ForeignKey(Printer, null=True, blank=True, on_delete=models.PROTECT)
    supplies = models.JSONField(default=list)
    finish = models.CharField(max_length=100, blank=True)
    calculation_model = models.ForeignKey(CalculationModel, null=True, on_delete=models.PROTECT)
    unit_cost = models.DecimalField(**MONEY)
    unit_price = models.DecimalField(**MONEY)
    snapshot = models.JSONField(default=dict)

class Order(CompanyModel):
    code = models.CharField(max_length=12)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    request = models.ForeignKey(QuoteRequest, on_delete=models.PROTECT)
    quote = models.OneToOneField(Quote, null=True, blank=True, on_delete=models.PROTECT)
    deadline = models.DateField(null=True, blank=True)
    priority = models.BooleanField(default=False)
    value = models.DecimalField(**MONEY)
    predicted_cost = models.DecimalField(**MONEY)
    actual_cost = models.DecimalField(**MONEY)
    commercial_status = models.CharField(max_length=16, default='confirmed')
    financial_status = models.CharField(max_length=16, default='pending')
    calculation_status = models.CharField(max_length=16, default='pending')
    snapshot = models.JSONField(default=dict)
    class Meta: constraints = [models.UniqueConstraint(fields=['company','code'], name='unique_order_code')]

class ProductionDemand(CompanyModel):
    STAGES = [('art','Fazer arte'),('material','Aguardando material'),('queue','Aguardando impressão'),('printing','Imprimindo'),('ready','Pronto')]
    code = models.CharField(max_length=12)
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT, related_name='demands')
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.PROTECT)
    quantity = models.DecimalField(**QTY)
    stage = models.CharField(max_length=12, choices=STAGES, default='art')
    deadline = models.DateField(null=True, blank=True)
    printer = models.ForeignKey(Printer, null=True, blank=True, on_delete=models.PROTECT)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.TextField(blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)

class ProductionFailure(CompanyModel):
    demand = models.ForeignKey(ProductionDemand, on_delete=models.PROTECT, related_name='failures')
    reason = models.CharField(max_length=180)
    material = models.ForeignKey(Material, null=True, blank=True, on_delete=models.PROTECT)
    additional_grams = models.DecimalField(**QTY)
    additional_minutes = models.PositiveIntegerField(default=0)
    additional_supplies = models.JSONField(default=list)
    notes = models.TextField(blank=True)
