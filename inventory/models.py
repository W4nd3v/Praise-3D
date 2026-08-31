from django.core.exceptions import ValidationError
from django.db import models
from core.models import CompanyModel, MONEY, QTY, PERCENT

class Printer(CompanyModel):
    name = models.CharField(max_length=100)
    model = models.CharField(max_length=100, blank=True)
    acquisition_cost = models.DecimalField(**MONEY)
    useful_life_hours = models.PositiveIntegerField(default=10000)
    residual_percent = models.DecimalField(**PERCENT)
    power_watts = models.PositiveIntegerField(default=350)

class CalculationModel(CompanyModel):
    MODE = [('margin','Margem'),('markup','Markup')]
    name = models.CharField(max_length=100)
    version = models.PositiveIntegerField(default=1)
    mode = models.CharField(max_length=10, choices=MODE, default='markup')
    rate = models.DecimalField(**PERCENT)
    components = models.JSONField(default=dict)
    def clean(self):
        if self.mode == 'margin' and self.rate >= 100: raise ValidationError('Margem deve ser menor que 100%.')

class Material(CompanyModel):
    TYPES = [('filament','Filamento'),('supply','Insumo')]
    type = models.CharField(max_length=12, choices=TYPES)
    name = models.CharField(max_length=140)
    color = models.CharField(max_length=60, blank=True)
    brand = models.CharField(max_length=80, blank=True)
    diameter = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    nominal_weight = models.DecimalField(**QTY)
    unit = models.CharField(max_length=20, default='un')
    supplier = models.CharField(max_length=140, blank=True)
    roll_cost = models.DecimalField(**MONEY)
    unit_cost = models.DecimalField(**MONEY)
    minimum = models.DecimalField(**QTY)
    physical_stock = models.DecimalField(**QTY)
    reserved_stock = models.DecimalField(**QTY)
    closed_rolls = models.PositiveIntegerField(default=0)
    open_rolls = models.PositiveIntegerField(default=0)
    @property
    def available(self): return self.physical_stock - self.reserved_stock

class MaterialMovement(CompanyModel):
    TYPES = [('purchase','Compra'),('open','Abertura'),('finish','Finalização'),('reserve','Reserva'),('consume','Consumo'),('release','Liberação'),('adjust','Ajuste'),('reverse','Estorno')]
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name='movements')
    type = models.CharField(max_length=12, choices=TYPES)
    quantity = models.DecimalField(**QTY)
    source_type = models.CharField(max_length=40, blank=True)
    source_id = models.CharField(max_length=40, blank=True)
    idempotency_key = models.CharField(max_length=100, unique=True)
    notes = models.TextField(blank=True)

class Category(CompanyModel):
    name = models.CharField(max_length=100)

class Product(CompanyModel):
    name = models.CharField(max_length=180)
    sku = models.CharField(max_length=40)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='products/', blank=True)
    technical_sheet = models.JSONField(default=dict)
    calculation_model = models.ForeignKey(CalculationModel, null=True, on_delete=models.PROTECT)
    cost = models.DecimalField(**MONEY)
    price = models.DecimalField(**MONEY)
    stock = models.DecimalField(**QTY)
    minimum_stock = models.DecimalField(**QTY)
    target_stock = models.DecimalField(**QTY)
    class Meta: constraints = [models.UniqueConstraint(fields=['company','sku'], name='unique_company_sku')]

class StockMovement(CompanyModel):
    TYPES = [('production','Produção'),('sale','Venda'),('consignment','Consignação'),('return','Retorno'),('loss','Perda'),('adjust','Ajuste'),('transfer','Transferência'),('reverse','Estorno')]
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='stock_movements')
    type = models.CharField(max_length=16, choices=TYPES)
    quantity = models.DecimalField(**QTY)
    source_type = models.CharField(max_length=40)
    source_id = models.CharField(max_length=40)
    idempotency_key = models.CharField(max_length=100, unique=True)
    balance_after = models.DecimalField(**QTY)
    notes = models.TextField(blank=True)
