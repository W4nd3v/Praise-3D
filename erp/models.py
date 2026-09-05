from decimal import Decimal, ROUND_HALF_UP
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone


MONEY = {"max_digits": 14, "decimal_places": 2, "default": Decimal("0.00")}
QTY = {"max_digits": 14, "decimal_places": 3, "default": Decimal("0.000")}
PERCENT = {"max_digits": 7, "decimal_places": 3, "default": Decimal("0.000")}


def money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def default_component_rules():
    """Regras padrão do motor único de precificação.

    Cada componente pode participar de três bases independentes: cálculo do preço,
    custo direto e base sobre a qual a margem é aplicada.
    """
    enabled = {"calculate": True, "cost": True, "margin": True}
    operational = {"calculate": True, "cost": False, "margin": False}
    return {
        "material": enabled.copy(),
        "labor": enabled.copy(),
        "energy": enabled.copy(),
        "maintenance": operational.copy(),
        "depreciation": operational.copy(),
        "supplies": enabled.copy(),
        "waste": operational.copy(),
    }


COMPONENT_LABELS = {
    "material": "Filamento",
    "labor": "Mão de obra",
    "energy": "Energia",
    "maintenance": "Manutenção",
    "depreciation": "Depreciação",
    "supplies": "Insumos",
    "waste": "Perdas / desperdício",
}


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Company(TimeStampedModel):
    MATERIAL_COST_POLICIES = [
        ("weighted", "Média ponderada da família"),
        ("last", "Último custo da família"),
        ("manual", "Valor manual"),
    ]
    PRICING_METHODS = [("margin", "Margem"), ("markup", "Markup")]

    name = models.CharField(max_length=160)
    trading_name = models.CharField(max_length=160, blank=True)
    slug = models.SlugField(unique=True)
    document = models.CharField(max_length=24, blank=True)
    state_registration = models.CharField(max_length=30, blank=True)
    responsible_name = models.CharField(max_length=160, blank=True)
    slogan = models.CharField(max_length=180, blank=True, default="A pioneira do 3D")
    logo = models.ImageField(upload_to="companies/", blank=True)
    primary_color = models.CharField(max_length=7, default="#1769e8")
    secondary_color = models.CharField(max_length=7, default="#ffb21c")
    success_color = models.CharField(max_length=7, default="#16a263")
    warning_color = models.CharField(max_length=7, default="#ef4444")
    phone = models.CharField(max_length=24, blank=True)
    whatsapp = models.CharField(max_length=24, blank=True)
    email = models.EmailField(blank=True)
    instagram = models.CharField(max_length=100, blank=True)
    website = models.URLField(blank=True)
    address = models.CharField(max_length=220, blank=True)
    city = models.CharField(max_length=90, blank=True)
    state = models.CharField(max_length=2, blank=True)
    postal_code = models.CharField(max_length=12, blank=True)
    energy_rate = models.DecimalField("Tarifa kWh", **MONEY)
    labor_hour_rate = models.DecimalField("Mão de obra por hora", **MONEY)
    fixed_cost_per_order = models.DecimalField(**MONEY)
    waste_percent = models.DecimalField(**PERCENT)
    default_margin_percent = models.DecimalField(**PERCENT)
    default_filament_minimum = models.PositiveIntegerField(default=2)
    material_cost_policy = models.CharField(max_length=12, choices=MATERIAL_COST_POLICIES, default="weighted")
    pricing_method = models.CharField(max_length=10, choices=PRICING_METHODS, default="margin")
    receivable_days_after_completion = models.PositiveSmallIntegerField(default=0)
    require_order_advance = models.BooleanField(default=False)
    order_advance_percent = models.DecimalField(**PERCENT)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.trading_name or self.name


class Membership(TimeStampedModel):
    ROLES = [
        ("admin", "Administrador"),
        ("operator", "Operação"),
        ("finance", "Financeiro"),
        ("viewer", "Somente leitura"),
    ]
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="erp_memberships")
    role = models.CharField(max_length=12, choices=ROLES, default="operator")
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "user"], name="uniq_company_user")]


class CompanyOwned(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)

    class Meta:
        abstract = True


class Sequence(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    prefix = models.CharField(max_length=5)
    value = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "prefix"], name="uniq_company_sequence")]

    @classmethod
    def next(cls, company, prefix):
        with transaction.atomic():
            item, _ = cls.objects.select_for_update().get_or_create(company=company, prefix=prefix)
            item.value += 1
            item.save(update_fields=["value"])
            return f"{prefix}-{item.value:05d}"

    @classmethod
    def next_numeric(cls, company, prefix="SKU", width=4):
        with transaction.atomic():
            item, _ = cls.objects.select_for_update().get_or_create(company=company, prefix=prefix)
            item.value += 1
            while Product.objects.filter(company=company, sku=str(item.value).zfill(width)).exists():
                item.value += 1
            item.save(update_fields=["value"])
            return str(item.value).zfill(width)


class IdempotencyRecord(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    key = models.UUIDField(default=uuid.uuid4)
    operation = models.CharField(max_length=64)
    result_model = models.CharField(max_length=80, blank=True)
    result_id = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "key", "operation"], name="uniq_idempotent_operation")]


class AuditLog(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    entity = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=64)
    action = models.CharField(max_length=64)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)


class Customer(CompanyOwned):
    name = models.CharField(max_length=180)
    legal_name = models.CharField(max_length=180, blank=True)
    document = models.CharField(max_length=24, blank=True)
    phone = models.CharField(max_length=24, blank=True)
    whatsapp = models.CharField(max_length=24, blank=True)
    instagram = models.CharField(max_length=80, blank=True)
    email = models.EmailField(blank=True)
    city = models.CharField(max_length=90, blank=True)
    state = models.CharField(max_length=2, blank=True)
    notes = models.TextField(blank=True)
    anonymous = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_customer_name_company")]

    def __str__(self):
        return self.name

    @property
    def total_purchased(self):
        orders = self.orders.filter(active=True, cancelled_at__isnull=True).aggregate(total=Sum("value"))["total"] or Decimal("0")
        sales = self.sales.filter(active=True, cancelled_at__isnull=True).aggregate(total=Sum("gross_amount"))["total"] or Decimal("0")
        return orders + sales

    @property
    def total_received(self):
        payments = self.payments.filter(active=True, status="received", order__cancelled_at__isnull=True).aggregate(total=Sum("gross_amount"))["total"] or Decimal("0")
        sales = self.sales.filter(active=True, cancelled_at__isnull=True).aggregate(total=Sum("gross_amount"))["total"] or Decimal("0")
        return payments + sales

    @property
    def balance_due(self):
        return money(self.total_purchased - self.total_received)


class PaymentMethod(CompanyOwned):
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=[("cash", "Dinheiro"), ("pix", "PIX"), ("debit", "Débito"), ("credit", "Crédito"), ("transfer", "Transferência"), ("other", "Outro")])
    installments = models.PositiveSmallIntegerField(default=1)
    fee_percent = models.DecimalField(**PERCENT)
    days_to_receive = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["kind", "installments"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_payment_method_name")]

    def __str__(self):
        return self.name


class Printer(CompanyOwned):
    name = models.CharField(max_length=120)
    model = models.CharField(max_length=120, blank=True)
    acquisition_cost = models.DecimalField(**MONEY)
    useful_life_hours = models.PositiveIntegerField(default=10000)
    residual_percent = models.DecimalField(**PERCENT)
    power_watts = models.PositiveIntegerField(default=0)
    maintenance_per_hour = models.DecimalField("Manutenção por hora", **MONEY)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def depreciation_per_hour(self):
        if not self.useful_life_hours:
            return Decimal("0")
        residual = self.acquisition_cost * (self.residual_percent / Decimal("100"))
        return money((self.acquisition_cost - residual) / self.useful_life_hours)


class MaterialFamily(CompanyOwned):
    name = models.CharField(max_length=80)
    reference_cost_kg = models.DecimalField(**MONEY)
    manual_cost_kg = models.DecimalField(**MONEY)
    last_cost_kg = models.DecimalField(**MONEY)
    weighted_cost_kg = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_material_family")]

    def __str__(self):
        return self.name

    def refresh_reference_cost(self):
        policy = self.company.material_cost_policy
        selected = {"manual": self.manual_cost_kg, "last": self.last_cost_kg, "weighted": self.weighted_cost_kg}.get(policy)
        if selected and selected > 0:
            self.reference_cost_kg = selected
            self.save(update_fields=["reference_cost_kg", "updated_at"])


class Filament(CompanyOwned):
    family = models.ForeignKey(MaterialFamily, on_delete=models.PROTECT, related_name="filaments")
    color = models.CharField(max_length=80)
    color_hex = models.CharField(max_length=7, blank=True)
    brand = models.CharField(max_length=100, blank=True)
    diameter_mm = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("1.75"))
    nominal_weight_g = models.PositiveIntegerField(default=1000)
    supplier = models.CharField(max_length=140, blank=True)
    unit_cost = models.DecimalField(**MONEY)
    closed_rolls = models.PositiveIntegerField(default=0)
    open_rolls = models.PositiveIntegerField(default=0)
    minimum_rolls = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["family__name", "color"]
        constraints = [models.UniqueConstraint(fields=["company", "family", "color", "brand", "diameter_mm"], name="uniq_filament_variant")]

    def __str__(self):
        return f"{self.family.name} / {self.color}"

    @property
    def effective_minimum(self):
        return self.minimum_rolls if self.minimum_rolls is not None else self.company.default_filament_minimum

    @property
    def stock_status(self):
        if self.closed_rolls == 0:
            return "critical"
        if self.closed_rolls <= self.effective_minimum:
            return "warning"
        return "ok"


class Supply(CompanyOwned):
    name = models.CharField(max_length=140)
    unit = models.CharField(max_length=24, default="un")
    supplier = models.CharField(max_length=140, blank=True)
    unit_cost = models.DecimalField(**MONEY)
    physical_stock = models.DecimalField(**QTY)
    reserved_stock = models.DecimalField(**QTY)
    minimum_stock = models.DecimalField(**QTY)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_supply_name")]

    def __str__(self):
        return self.name

    @property
    def stock_status(self):
        if self.available_stock < 0:
            return "critical"
        if self.available_stock <= self.minimum_stock:
            return "warning"
        return "ok"

    @property
    def available_stock(self):
        return self.physical_stock - self.reserved_stock


class CalculationModel(CompanyOwned):
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    pricing_method = models.CharField(max_length=10, choices=Company.PRICING_METHODS, default="margin")
    margin_percent = models.DecimalField(**PERCENT)
    include_material = models.BooleanField(default=True)
    include_energy = models.BooleanField(default=True)
    include_depreciation = models.BooleanField(default=True)
    include_labor = models.BooleanField(default=True)
    include_supplies = models.BooleanField(default=True)
    include_waste = models.BooleanField(default=True)
    include_fixed_cost = models.BooleanField(default=True)
    tax_percent = models.DecimalField(**PERCENT)
    component_rules = models.JSONField(default=default_component_rules, blank=True)
    default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-default", "name"]

    def __str__(self):
        return self.name

    def normalized_rules(self):
        normalized = default_component_rules()
        for component, defaults in normalized.items():
            incoming = (self.component_rules or {}).get(component, {})
            defaults.update({key: bool(incoming.get(key, defaults[key])) for key in defaults})
            if not defaults["calculate"]:
                defaults["cost"] = False
                defaults["margin"] = False
        return normalized

    @property
    def component_rows(self):
        rules = self.normalized_rules()
        return [{"key": key, "label": COMPONENT_LABELS[key], **rules[key]} for key in COMPONENT_LABELS]


class CustomCostComponent(CompanyOwned):
    BASES = [
        ("order", "Fixo por pedido"),
        ("item", "Fixo por item"),
        ("gram", "Por grama"),
        ("hour", "Por hora"),
        ("unit", "Por unidade"),
        ("percent", "Percentual sobre custo"),
    ]
    calculation_model = models.ForeignKey(CalculationModel, on_delete=models.CASCADE, related_name="custom_components")
    name = models.CharField(max_length=100)
    basis = models.CharField(max_length=10, choices=BASES)
    value = models.DecimalField(max_digits=14, decimal_places=4, default=0)


class Composition(CompanyOwned):
    name = models.CharField(max_length=180, default="Composição")
    calculation_model = models.ForeignKey(CalculationModel, on_delete=models.PROTECT)
    labor_minutes = models.PositiveIntegerField(default=0)
    discount_percent = models.DecimalField(**PERCENT)
    margin_override = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    waste_override = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    material_cost = models.DecimalField(**MONEY)
    print_cost = models.DecimalField(**MONEY)
    energy_cost = models.DecimalField(**MONEY)
    maintenance_cost = models.DecimalField(**MONEY)
    depreciation_cost = models.DecimalField(**MONEY)
    labor_cost = models.DecimalField(**MONEY)
    supplies_cost = models.DecimalField(**MONEY)
    extras_cost = models.DecimalField(**MONEY)
    base_calculation = models.DecimalField(**MONEY)
    direct_cost = models.DecimalField(**MONEY)
    margin_base = models.DecimalField(**MONEY)
    margin_value = models.DecimalField(**MONEY)
    total_cost = models.DecimalField(**MONEY)
    suggested_price = models.DecimalField(**MONEY)
    final_price = models.DecimalField(**MONEY)
    predicted_profit = models.DecimalField(**MONEY)
    margin_percent = models.DecimalField(**PERCENT)
    snapshot = models.JSONField(default=dict, blank=True)
    calculated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name

    @property
    def is_calculated(self):
        return bool(self.calculated_at)

    def recalculate(self, commit=True):
        model = self.calculation_model
        company = self.company
        decimal_value = lambda value: Decimal(str(value or 0))
        rules = model.normalized_rules()
        waste_rate = decimal_value(self.waste_override if self.waste_override is not None else company.waste_percent)
        material = energy = maintenance = depreciation = supplies = Decimal("0")
        print_hours = grams = units = Decimal("0")
        item_count = 0
        part_snapshot = []
        supply_snapshot = []
        for item in self.items.filter(active=True).prefetch_related("parts__material_family", "parts__printer", "supplies__supply"):
            item_count += 1
            units += item.quantity
            for part in item.parts.filter(active=True):
                # Gramas e minutos descrevem uma mesa completa. O rateio é
                # proporcional e deliberadamente não arredonda mesas parciais.
                plate_quantity = Decimal(part.plate_quantity or 1)
                multiplier = (item.quantity * part.quantity) / plate_quantity
                part_grams = part.grams * multiplier
                hours = (Decimal(part.print_minutes) / Decimal("60")) * multiplier
                part_material = (part_grams / Decimal("1000")) * part.material_family.reference_cost_kg
                material += part_material
                part_energy = part_maintenance = part_depreciation = Decimal("0")
                print_hours += hours
                if part.printer:
                    part_energy = (Decimal(part.printer.power_watts) / Decimal("1000")) * hours * decimal_value(company.energy_rate)
                    part_maintenance = part.printer.maintenance_per_hour * hours
                    part_depreciation = part.printer.depreciation_per_hour * hours
                    energy += part_energy
                    maintenance += part_maintenance
                    depreciation += part_depreciation
                part_components = {"material": part_material, "energy": part_energy, "maintenance": part_maintenance, "depreciation": part_depreciation, "waste": part_material * waste_rate / 100}
                grams += part_grams
                part_snapshot.append({
                    "item": item.name,
                    "part_id": part.pk,
                    "part": part.name,
                    "family": part.material_family.name,
                    "plate_quantity": part.plate_quantity,
                    "plate_grams": str(part.grams),
                    "plate_minutes": part.print_minutes,
                    "part_quantity_per_item": str(part.quantity),
                    "item_quantity": str(item.quantity),
                    "rated_grams": str(part_grams),
                    "rated_hours": str(hours),
                    # Chaves antigas continuam no snapshot para leitores históricos.
                    "grams": str(part_grams),
                    "minutes": str(hours * Decimal("60")),
                    "reference_cost_kg": str(part.material_family.reference_cost_kg),
                    "snapshot_cost": str(money(sum((amount for key, amount in part_components.items() if rules[key]["cost"]), Decimal("0")))),
                    "direct_components": {key: str(money(amount)) for key, amount in part_components.items()},
                    "maintenance_cost": str(money((part.printer.maintenance_per_hour * hours) if part.printer else 0)),
                    "printer": part.printer.name if part.printer else "",
                })
            for use in item.supplies.filter(active=True):
                quantity = use.quantity * item.quantity
                line_cost = quantity * use.supply.unit_cost
                supplies += line_cost
                supply_snapshot.append({"item": item.name, "supply": use.supply.name, "quantity": str(quantity), "unit_cost": str(use.supply.unit_cost)})

        labor = (Decimal(self.labor_minutes) / Decimal("60")) * decimal_value(company.labor_hour_rate)
        waste = material * (waste_rate / Decimal("100"))

        extras = Decimal("0")
        for component in model.custom_components.filter(active=True):
            if component.basis == "order":
                extras += component.value
            elif component.basis == "item":
                extras += component.value * item_count
            elif component.basis == "gram":
                extras += component.value * grams
            elif component.basis == "hour":
                extras += component.value * print_hours
            elif component.basis == "unit":
                extras += component.value * units
            elif component.basis == "percent":
                extras += (material + labor + energy + maintenance + depreciation + supplies + waste) * (component.value / Decimal("100"))

        rules = model.normalized_rules()
        components = {
            "material": material,
            "labor": labor,
            "energy": energy,
            "maintenance": maintenance,
            "depreciation": depreciation,
            "supplies": supplies + decimal_value(company.fixed_cost_per_order) + extras,
            "waste": waste,
        }
        calculation_base = sum((value for key, value in components.items() if rules[key]["calculate"]), Decimal("0"))
        direct_cost = sum((value for key, value in components.items() if rules[key]["cost"]), Decimal("0"))
        margin_base = sum((value for key, value in components.items() if rules[key]["margin"]), Decimal("0"))
        rate = decimal_value(self.margin_override if self.margin_override is not None else model.margin_percent)
        margin_value = margin_base * (rate / Decimal("100"))
        suggested = calculation_base + margin_value
        # Legacy technical adjustments do not change the B + (M * p) suggestion.
        final = suggested * (Decimal("1") + decimal_value(model.tax_percent) / Decimal("100"))
        final *= Decimal("1") - decimal_value(self.discount_percent) / Decimal("100")
        profit = final - calculation_base
        actual_margin = (profit / margin_base * Decimal("100")) if margin_base else Decimal("0")
        values = {
            "material_cost": money(material),
            "print_cost": money(energy + maintenance + depreciation),
            "energy_cost": money(energy),
            "maintenance_cost": money(maintenance),
            "depreciation_cost": money(depreciation),
            "labor_cost": money(labor),
            "supplies_cost": money(supplies),
            "extras_cost": money(extras),
            "base_calculation": money(calculation_base),
            "direct_cost": money(direct_cost),
            "margin_base": money(margin_base),
            "margin_value": money(margin_value),
            "total_cost": money(calculation_base),
            "suggested_price": money(suggested),
            "final_price": money(final),
            "predicted_profit": money(profit),
            "margin_percent": actual_margin.quantize(Decimal("0.001")),
        }
        for field, value in values.items():
            setattr(self, field, value)
        self.calculated_at = timezone.now()
        self.snapshot = {
            "calculated_at": self.calculated_at.isoformat(),
            "model": model.name,
            "pricing_method": model.pricing_method,
            "rate_percent": str(rate),
            "tax_percent": str(model.tax_percent),
            "energy_rate": str(company.energy_rate),
            "labor_hour_rate": str(company.labor_hour_rate),
            "waste_percent": str(waste_rate),
            "component_rules": rules,
            "components": {key: str(money(value)) for key, value in components.items()},
            "parts": part_snapshot,
            "supplies": supply_snapshot,
            "totals": {key: str(value) for key, value in values.items()},
        }
        if commit:
            self.save()
        return values


class CompositionItem(CompanyOwned):
    composition = models.ForeignKey(Composition, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    quantity = models.DecimalField(**QTY)
    unit = models.CharField(max_length=16, default="un")

    class Meta:
        ordering = ["created_at"]


class ManufacturingPart(CompanyOwned):
    item = models.ForeignKey(CompositionItem, on_delete=models.CASCADE, related_name="parts")
    name = models.CharField(max_length=140)
    material_family = models.ForeignKey(MaterialFamily, on_delete=models.PROTECT)
    grams = models.DecimalField(**QTY)
    print_minutes = models.PositiveIntegerField(default=0)
    printer = models.ForeignKey(Printer, null=True, blank=True, on_delete=models.PROTECT)
    quantity = models.DecimalField(**QTY)
    plate_quantity = models.PositiveIntegerField("Qnt. na mesa", default=1)

    def clean(self):
        if self.plate_quantity < 1:
            raise ValidationError("Qnt. na mesa deve ser um número inteiro maior ou igual a 1.")


class CompositionSupply(CompanyOwned):
    item = models.ForeignKey(CompositionItem, on_delete=models.CASCADE, related_name="supplies")
    supply = models.ForeignKey(Supply, on_delete=models.PROTECT)
    quantity = models.DecimalField(**QTY)


class QuoteRequest(CompanyOwned):
    ORIGINS = [("whatsapp", "WhatsApp"), ("instagram", "Instagram"), ("store", "Presencial"), ("phone", "Telefone"), ("referral", "Indicação"), ("other", "Outro")]
    STATUSES = [("new", "Nova"), ("analysis", "Em análise"), ("waiting", "Aguardando retorno"), ("quoted", "Orçamento criado"), ("ordered", "Pedido criado"), ("cancelled", "Cancelada")]
    code = models.CharField(max_length=16)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="quote_requests")
    description = models.TextField()
    notes = models.TextField(blank=True)
    origin = models.CharField(max_length=16, choices=ORIGINS)
    reminder_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUSES, default="new")

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_request_code")]

    def __str__(self):
        return f"{self.code} - {self.description}"


class Quote(CompanyOwned):
    STATUSES = [("draft", "Em elaboração"), ("sent", "Enviado"), ("waiting", "Aguardando retorno"), ("approved", "Aprovado"), ("converted", "Convertido"), ("expired", "Expirado"), ("cancelled", "Cancelado")]
    code = models.CharField(max_length=16)
    request = models.OneToOneField(QuoteRequest, on_delete=models.PROTECT, related_name="quote")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="quotes")
    composition = models.OneToOneField(Composition, on_delete=models.PROTECT, related_name="quote")
    status = models.CharField(max_length=16, choices=STATUSES, default="draft")
    valid_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    payment_terms = models.CharField(max_length=220, blank=True)
    freight_amount = models.DecimalField(**MONEY)
    manual_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_quote_code")]

    def __str__(self):
        return self.code

    @property
    def expected_profit(self):
        if self.manual_value is None:
            return None
        return money(self.manual_value - self.composition.base_calculation)

    @property
    def effective_margin(self):
        if self.expected_profit is None or not self.composition.margin_base:
            return None
        return (self.expected_profit / self.composition.margin_base * Decimal("100")).quantize(Decimal("0.001"))


class ProductCategory(CompanyOwned):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_product_category")]

    def __str__(self):
        return self.name


class ProductType(CompanyOwned):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="uniq_product_type")]

    def __str__(self):
        return self.name


class Product(CompanyOwned):
    name = models.CharField(max_length=180)
    sku = models.CharField(max_length=40)
    category = models.CharField(max_length=100, blank=True)
    category_ref = models.ForeignKey(ProductCategory, null=True, blank=True, on_delete=models.PROTECT, related_name="products")
    product_type = models.ForeignKey(ProductType, null=True, blank=True, on_delete=models.PROTECT, related_name="products")
    image = models.ImageField(upload_to="products/", blank=True)
    description = models.TextField(blank=True)
    composition = models.OneToOneField(Composition, null=True, blank=True, on_delete=models.PROTECT, related_name="product")
    minimum_stock = models.DecimalField(**QTY)
    target_stock = models.DecimalField(**QTY)
    current_stock = models.DecimalField(**QTY)
    current_cost = models.DecimalField(**MONEY)
    current_price = models.DecimalField(**MONEY)
    operational_activity = models.BooleanField("Em atividade", default=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="deactivated_products")

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "sku"], name="uniq_product_sku")]

    def __str__(self):
        return f"{self.sku} - {self.name}"

    @property
    def display_category(self):
        return self.category_ref.name if self.category_ref else self.category

    @property
    def stock_status(self):
        if not self.active:
            return "inactive"
        if not self.operational_activity:
            return "paused"
        if self.current_stock < 0:
            return "critical"
        if self.current_stock <= self.minimum_stock:
            return "warning"
        return "ok"

    @property
    def suggested_replenishment(self):
        return max(Decimal("0"), self.target_stock - self.current_stock)


class Order(CompanyOwned):
    PRIORITIES = [("normal", "Normal"), ("priority", "Prioritário"), ("urgent", "Urgente")]
    CALC_STATUSES = [("pending", "Pendente"), ("completed", "Concluído")]
    FIN_STATUSES = [("pending", "Pendente"), ("partial", "Parcialmente pago"), ("paid", "Quitado")]
    code = models.CharField(max_length=16)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    request = models.ForeignKey(QuoteRequest, null=True, blank=True, on_delete=models.PROTECT, related_name="orders")
    quote = models.OneToOneField(Quote, null=True, blank=True, on_delete=models.PROTECT, related_name="order")
    composition = models.OneToOneField(Composition, on_delete=models.PROTECT, related_name="order")
    description = models.TextField()
    deadline = models.DateField(null=True, blank=True)
    priority = models.BooleanField(default=False)
    priority_level = models.CharField(max_length=12, choices=PRIORITIES, default="normal")
    value = models.DecimalField(**MONEY)
    predicted_cost = models.DecimalField(**MONEY)
    actual_cost = models.DecimalField(**MONEY)
    calculation_status = models.CharField(max_length=12, choices=CALC_STATUSES, default="pending")
    financial_status = models.CharField(max_length=12, choices=FIN_STATUSES, default="pending")
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_order_code")]

    def __str__(self):
        return self.code

    @property
    def received(self):
        return self.payments.filter(status="received").aggregate(total=Sum("gross_amount"))["total"] or Decimal("0")

    @property
    def balance(self):
        return money(self.value - self.received)

    @property
    def predicted_profit(self):
        return money(self.value - self.predicted_cost)

    @property
    def real_profit(self):
        return money(self.value - self.actual_cost)

    @property
    def operation(self):
        from .operations import order_state
        return order_state(self)

    @property
    def is_overdue(self):
        return bool(self.deadline and self.deadline < timezone.localdate() and not self.delivered_at and not self.cancelled_at)


class ProductionDemand(CompanyOwned):
    STAGES = [("art", "Fazer arte"), ("material", "Aguardando material"), ("queue", "Aguardando impressão"), ("printing", "Imprimindo"), ("ready", "Pronto")]
    ORIGINS = [("order", "Pedido"), ("replenishment", "Reposição de estoque")]
    code = models.CharField(max_length=16)
    origin = models.CharField(max_length=16, choices=ORIGINS)
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT, related_name="demands")
    composition_item = models.ForeignKey(CompositionItem, null=True, blank=True, on_delete=models.PROTECT, related_name="demands")
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.PROTECT, related_name="demands")
    item_name = models.TextField()
    quantity = models.DecimalField(**QTY)
    stage = models.CharField(max_length=12, choices=STAGES)
    deadline = models.DateField(null=True, blank=True)
    priority = models.BooleanField(default=False)
    printer = models.ForeignKey(Printer, null=True, blank=True, on_delete=models.PROTECT)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.TextField(blank=True)
    reserved_supplies = models.JSONField(default=dict, blank=True)
    consumed_supplies = models.JSONField(default=dict, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    completed_stock_movement = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="cancelled_production_demands")

    class Meta:
        ordering = ["-priority", "deadline", "created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_demand_code")]

    def __str__(self):
        return self.code


class ProductionFailure(CompanyOwned):
    demand = models.ForeignKey(ProductionDemand, on_delete=models.PROTECT, related_name="failures")
    part = models.ForeignKey(ManufacturingPart, null=True, blank=True, on_delete=models.PROTECT, related_name="failures")
    failure_percent = models.DecimalField(**PERCENT)
    reason = models.CharField(max_length=180)
    additional_grams = models.DecimalField(**QTY)
    additional_minutes = models.PositiveIntegerField(default=0)
    additional_supplies = models.JSONField(default=list, blank=True)
    additional_cost = models.DecimalField(**MONEY)
    notes = models.TextField(blank=True)


class StockMovement(CompanyOwned):
    TYPES = [("production", "Produção"), ("sale", "Venda"), ("consignment", "Consignação"), ("consignment_return", "Retorno de consignação"), ("return", "Devolução"), ("loss", "Perda"), ("adjustment", "Ajuste"), ("transfer", "Transferência")]
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="stock_movements")
    movement_type = models.CharField(max_length=24, choices=TYPES)
    quantity = models.DecimalField(**QTY)
    balance_after = models.DecimalField(**QTY)
    source_type = models.CharField(max_length=60, blank=True)
    source_id = models.CharField(max_length=64, blank=True)
    note = models.CharField(max_length=240, blank=True)
    location = models.CharField(max_length=180, blank=True, default="Estoque central")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-created_at"]


class FinancialAccount(CompanyOwned):
    KINDS = [("cash", "Caixa"), ("checking", "Conta corrente"), ("digital", "Conta digital"), ("wallet", "Carteira"), ("other", "Outro")]
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=KINDS, default="checking")
    institution = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)
    opening_balance = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["-is_default", "name"]
        constraints = [models.UniqueConstraint(fields=["company"], condition=models.Q(is_default=True), name="uniq_default_financial_account")]

    def __str__(self):
        return self.name

    @property
    def balance(self):
        entries = self.entries.filter(status="paid")
        incoming = entries.filter(direction="in").aggregate(total=Sum("net_amount"))["total"] or Decimal("0")
        outgoing = entries.filter(direction="out").aggregate(total=Sum("net_amount"))["total"] or Decimal("0")
        return money(self.opening_balance + incoming - outgoing)


class FinancialEntry(CompanyOwned):
    DIRECTIONS = [("in", "Entrada"), ("out", "Saída")]
    STATUSES = [("pending", "Pendente"), ("paid", "Pago/Recebido"), ("cancelled", "Cancelado")]
    code = models.CharField(max_length=16)
    direction = models.CharField(max_length=3, choices=DIRECTIONS)
    description = models.CharField(max_length=220)
    category = models.CharField(max_length=100)
    account = models.ForeignKey(FinancialAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="entries")
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name="financial_entries")
    supplier = models.CharField(max_length=140, blank=True)
    payment_method = models.ForeignKey(PaymentMethod, null=True, blank=True, on_delete=models.PROTECT)
    gross_amount = models.DecimalField(**MONEY)
    fee_amount = models.DecimalField(**MONEY)
    net_amount = models.DecimalField(**MONEY)
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(default=timezone.localdate)
    paid_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default="pending")
    installment_number = models.PositiveSmallIntegerField(default=1)
    installments_total = models.PositiveSmallIntegerField(default=1)
    source_type = models.CharField(max_length=60, blank=True)
    source_id = models.CharField(max_length=64, blank=True)
    notes = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-issue_date", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_financial_code")]


class Payment(CompanyOwned):
    STATUSES = [("pending", "Pendente"), ("received", "Recebido"), ("cancelled", "Cancelado")]
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="payments")
    financial_entry = models.OneToOneField(FinancialEntry, on_delete=models.PROTECT, related_name="payment")
    method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT)
    gross_amount = models.DecimalField(**MONEY)
    fee_amount = models.DecimalField(**MONEY)
    net_amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=12, choices=STATUSES, default="received")
    snapshot = models.JSONField(default=dict, blank=True)


class Sale(CompanyOwned):
    code = models.CharField(max_length=16)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="sales")
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT)
    gross_amount = models.DecimalField(**MONEY)
    fee_amount = models.DecimalField(**MONEY)
    net_amount = models.DecimalField(**MONEY)
    cost_amount = models.DecimalField(**MONEY)
    profit_amount = models.DecimalField(**MONEY)
    financial_entry = models.OneToOneField(FinancialEntry, on_delete=models.PROTECT, related_name="sale")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_sale_code")]


class SaleItem(CompanyOwned):
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(**QTY)
    unit_price = models.DecimalField(**MONEY)
    unit_cost = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)
    snapshot = models.JSONField(default=dict, blank=True)


class Purchase(CompanyOwned):
    STATUSES = [("draft", "Rascunho"), ("pending", "Pendente de efetivação"), ("effected", "Efetivada"), ("cancelled", "Cancelada")]
    TYPES = [("filament", "Filamento"), ("supply", "Insumo")]
    code = models.CharField(max_length=16)
    supplier = models.CharField(max_length=160)
    purchase_date = models.DateField(default=timezone.localdate)
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT)
    account = models.ForeignKey(FinancialAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="purchases")
    installments = models.PositiveSmallIntegerField(default=1)
    first_due_date = models.DateField(default=timezone.localdate)
    total = models.DecimalField(**MONEY)
    notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    source_demand = models.ForeignKey(ProductionDemand, null=True, blank=True, on_delete=models.PROTECT, related_name="purchases")
    status = models.CharField(max_length=12, choices=STATUSES, default="draft")
    purchase_type = models.CharField(max_length=12, choices=TYPES, default="supply")

    class Meta:
        ordering = ["-purchase_date"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_purchase_code")]


class PurchaseItem(CompanyOwned):
    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="items")
    filament = models.ForeignKey(Filament, null=True, blank=True, on_delete=models.PROTECT)
    supply = models.ForeignKey(Supply, null=True, blank=True, on_delete=models.PROTECT)
    quantity = models.DecimalField(**QTY)
    unit_cost = models.DecimalField(**MONEY)
    total = models.DecimalField(**MONEY)

    def clean(self):
        if bool(self.filament) == bool(self.supply):
            raise ValidationError("Selecione um filamento ou um insumo.")


class MaterialMovement(CompanyOwned):
    TYPES = [("purchase", "Compra"), ("open_roll", "Abertura de rolo"), ("close_roll", "Finalização de rolo"), ("production", "Produção"), ("adjustment", "Ajuste")]
    movement_type = models.CharField(max_length=20, choices=TYPES)
    filament = models.ForeignKey(Filament, null=True, blank=True, on_delete=models.PROTECT)
    supply = models.ForeignKey(Supply, null=True, blank=True, on_delete=models.PROTECT)
    quantity = models.DecimalField(**QTY)
    source_type = models.CharField(max_length=60, blank=True)
    source_id = models.CharField(max_length=64, blank=True)
    note = models.CharField(max_length=220, blank=True)
    details = models.JSONField(default=dict, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-created_at"]


class ConsignedStore(CompanyOwned):
    name = models.CharField(max_length=160)
    contact_name = models.CharField(max_length=140, blank=True)
    phone = models.CharField(max_length=24, blank=True)
    address = models.CharField(max_length=240, blank=True)
    city = models.CharField(max_length=90, blank=True)
    state = models.CharField(max_length=2, blank=True)
    postal_code = models.CharField(max_length=12, blank=True)
    district = models.CharField(max_length=100, blank=True)
    street_number = models.CharField(max_length=20, blank=True)
    complement = models.CharField(max_length=120, blank=True)
    default_commission_percent = models.DecimalField(**PERCENT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]


class ConsignmentBalance(CompanyOwned):
    store = models.ForeignKey(ConsignedStore, on_delete=models.PROTECT, related_name="balances")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="consignment_balances")
    quantity = models.DecimalField(**QTY)
    reference_price = models.DecimalField(**MONEY)
    commission_percent = models.DecimalField(**PERCENT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "store", "product"], name="uniq_consignment_balance")]


class ConsignmentShipment(CompanyOwned):
    code = models.CharField(max_length=16)
    store = models.ForeignKey(ConsignedStore, on_delete=models.PROTECT, related_name="shipments")
    shipment_date = models.DateField(default=timezone.localdate)
    notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-shipment_date"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_shipment_code")]


class ConsignmentShipmentItem(CompanyOwned):
    shipment = models.ForeignKey(ConsignmentShipment, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(**QTY)
    reference_price = models.DecimalField(**MONEY)
    commission_percent = models.DecimalField(**PERCENT)
    snapshot = models.JSONField(default=dict, blank=True)


class ConsignmentSettlement(CompanyOwned):
    code = models.CharField(max_length=16)
    store = models.ForeignKey(ConsignedStore, on_delete=models.PROTECT, related_name="settlements")
    period_reference = models.CharField(max_length=20)
    settlement_date = models.DateField(default=timezone.localdate)
    units_sold = models.DecimalField(**QTY)
    gross_amount = models.DecimalField(**MONEY)
    commission_amount = models.DecimalField(**MONEY)
    net_amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=16, choices=[("draft", "Em conferência"), ("blocked", "Divergência"), ("completed", "Concluída")], default="draft")
    financial_entry = models.OneToOneField(FinancialEntry, null=True, blank=True, on_delete=models.PROTECT)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-settlement_date"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_settlement_code")]


class ConsignmentSettlementItem(CompanyOwned):
    DISPOSITIONS = [("remain", "Permanecer consignado"), ("return", "Retornar ao estoque central")]
    settlement = models.ForeignKey(ConsignmentSettlement, on_delete=models.PROTECT, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    expected_quantity = models.DecimalField(**QTY)
    found_quantity = models.DecimalField(**QTY)
    sold_quantity = models.DecimalField(**QTY)
    reference_price = models.DecimalField(**MONEY)
    commission_percent = models.DecimalField(**PERCENT)
    gross_amount = models.DecimalField(**MONEY)
    commission_amount = models.DecimalField(**MONEY)
    net_amount = models.DecimalField(**MONEY)
    divergence = models.BooleanField(default=False)
    disposition = models.CharField(max_length=12, choices=DISPOSITIONS, default="remain")


class IssuedDocument(TimeStampedModel):
    """Cópia imutável do PDF efetivamente emitido para preservar o histórico."""
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="issued_documents")
    document_type = models.CharField(max_length=40)
    reference_id = models.CharField(max_length=100)
    filename = models.CharField(max_length=180)
    content = models.BinaryField()
    content_hash = models.CharField(max_length=64)
    snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "document_type", "reference_id"], name="uniq_issued_document")]


class Alert(CompanyOwned):
    LEVELS = [("info", "Informação"), ("warning", "Atenção"), ("critical", "Crítico")]
    level = models.CharField(max_length=12, choices=LEVELS)
    title = models.CharField(max_length=180)
    message = models.TextField()
    source_type = models.CharField(max_length=60, blank=True)
    source_id = models.CharField(max_length=64, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["resolved_at", "-created_at"]


class ActivityEvent(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    happened_at = models.DateTimeField(default=timezone.now, db_index=True)
    kind = models.CharField(max_length=48)
    description = models.TextField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name="activity_events")
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT, related_name="activity_events")
    request = models.ForeignKey(QuoteRequest, null=True, blank=True, on_delete=models.PROTECT, related_name="activity_events")
    source_model = models.CharField(max_length=64)
    source_id = models.PositiveBigIntegerField()
    event_key = models.CharField(max_length=180)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-happened_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["company", "event_key"], name="uniq_activity_event")]

    @property
    def url(self):
        from .operations import record_url
        return record_url(self.source_model, self.source_id) or (record_url("erp.Order", self.order_id) if self.order_id else record_url("erp.QuoteRequest", self.request_id) if self.request_id else record_url("erp.Customer", self.customer_id) if self.customer_id else "")


class RequestReminder(models.Model):
    STATUSES = [("scheduled", "Agendado"), ("due", "Vencido"), ("snoozed", "Adiado"), ("completed", "Concluído"), ("cancelled", "Cancelado")]
    PURPOSES = [("manual", "Retornar contato / tarefa manual"), ("quote", "Criar orçamento"), ("order", "Gerar pedido")]
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    request = models.OneToOneField(QuoteRequest, on_delete=models.PROTECT, related_name="reminder")
    original_at = models.DateTimeField()
    scheduled_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=12, choices=STATUSES, default="scheduled")
    purpose = models.CharField(max_length=12, choices=PURPOSES, default="manual")
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="assigned_reminders")
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="completed_reminders")
    completion_mode = models.CharField(max_length=24, blank=True)
    version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scheduled_at", "pk"]


class ArchivedRecord(models.Model):
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    source_model = models.CharField(max_length=64)
    source_id = models.PositiveBigIntegerField()
    archived_at = models.DateTimeField(default=timezone.now)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    archived = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "source_model", "source_id"], name="uniq_archived_record")]
