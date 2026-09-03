from calendar import monthrange
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import uuid

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    Alert,
    Composition,
    CompositionItem,
    CompositionSupply,
    ConsignmentBalance,
    ConsignmentSettlement,
    ConsignmentSettlementItem,
    ConsignmentShipment,
    ConsignmentShipmentItem,
    FinancialEntry,
    IdempotencyRecord,
    ManufacturingPart,
    MaterialMovement,
    Order,
    Payment,
    ProductionDemand,
    ProductionFailure,
    Purchase,
    Quote,
    QuoteRequest,
    Sale,
    SaleItem,
    Sequence,
    StockMovement,
    money,
)


def to_decimal(value, default="0"):
    try:
        return Decimal(str(value).replace(".", "").replace(",", ".")) if isinstance(value, str) and "," in value else Decimal(str(value))
    except Exception:
        return Decimal(default)


def add_months(value, months):
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def parse_key(key):
    try:
        return uuid.UUID(str(key))
    except (ValueError, TypeError, AttributeError):
        return uuid.uuid4()


def begin_once(company, key, operation):
    parsed = parse_key(key)
    record, created = IdempotencyRecord.objects.get_or_create(company=company, key=parsed, operation=operation)
    return record, created


def finish_once(record, result):
    record.result_model = result._meta.label
    record.result_id = str(result.pk)
    record.save(update_fields=["result_model", "result_id", "updated_at"])


def previous_result(record):
    if not record.result_model or not record.result_id:
        return None
    model = apps.get_model(record.result_model)
    return model.objects.get(pk=record.result_id)


def validate_company(company, *objects):
    for item in objects:
        if item is not None and getattr(item, "company_id", company.pk) != company.pk:
            raise ValidationError("Operação entre empresas diferentes não é permitida.")


@transaction.atomic
def clone_composition(source, name=None):
    source = Composition.objects.prefetch_related("items__parts", "items__supplies").get(pk=source.pk)
    clone = Composition.objects.create(
        company=source.company,
        name=(name or source.name)[:180],
        calculation_model=source.calculation_model,
        labor_minutes=source.labor_minutes,
        discount_percent=source.discount_percent,
        margin_override=source.margin_override,
        waste_override=source.waste_override,
    )
    part_ids = {}
    for item in source.items.filter(active=True):
        new_item = CompositionItem.objects.create(
            company=source.company,
            composition=clone,
            name=item.name,
            description=item.description,
            quantity=item.quantity,
            unit=item.unit,
        )
        for part in item.parts.filter(active=True):
            copied_part = ManufacturingPart.objects.create(
                company=source.company,
                item=new_item,
                name=part.name,
                material_family=part.material_family,
                grams=part.grams,
                print_minutes=part.print_minutes,
                printer=part.printer,
                quantity=part.quantity,
            )
            part_ids[part.pk] = copied_part.pk
        CompositionSupply.objects.bulk_create([
            CompositionSupply(company=source.company, item=new_item, supply=use.supply, quantity=use.quantity)
            for use in item.supplies.filter(active=True)
        ])
    if source.is_calculated:
        for field in ["material_cost", "print_cost", "energy_cost", "maintenance_cost", "depreciation_cost", "labor_cost", "supplies_cost", "extras_cost", "base_calculation", "direct_cost", "margin_base", "margin_value", "total_cost", "suggested_price", "final_price", "predicted_profit", "margin_percent", "calculated_at"]:
            setattr(clone, field, getattr(source, field))
        clone.snapshot = deepcopy(source.snapshot)
        for row in clone.snapshot.get("parts", []):
            row["part_id"] = part_ids.get(row.get("part_id"), row.get("part_id"))
        clone.save()
    return clone


@transaction.atomic
def request_to_quote(request_item, key):
    request_item = QuoteRequest.objects.select_for_update().get(pk=request_item.pk)
    record, created = begin_once(request_item.company, key, "request_to_quote")
    if not created:
        return previous_result(record)
    existing = getattr(request_item, "quote", None)
    if existing:
        finish_once(record, existing)
        return existing
    model = request_item.company.calculationmodel_set.filter(active=True, default=True).first()
    if not model:
        raise ValidationError("Cadastre um modelo de cálculo padrão em Parâmetros.")
    short_description = request_item.description[:180]
    composition = Composition.objects.create(company=request_item.company, name=short_description, calculation_model=model)
    CompositionItem.objects.create(company=request_item.company, composition=composition, name=short_description, description=request_item.description, quantity=1, unit="un")
    quote = Quote.objects.create(
        company=request_item.company,
        code=Sequence.next(request_item.company, "ORC"),
        request=request_item,
        customer=request_item.customer,
        composition=composition,
        valid_until=timezone.localdate() + timedelta(days=30),
    )
    request_item.status = "quoted"
    request_item.save(update_fields=["status", "updated_at"])
    finish_once(record, quote)
    return quote


@transaction.atomic
def request_to_direct_order(request_item, key, deadline=None):
    request_item = QuoteRequest.objects.select_for_update().get(pk=request_item.pk)
    record, created = begin_once(request_item.company, key, "request_to_direct_order")
    if not created:
        return previous_result(record)
    existing = request_item.orders.filter(active=True).first()
    if existing:
        finish_once(record, existing)
        return existing
    model = request_item.company.calculationmodel_set.filter(active=True, default=True).first()
    if not model:
        raise ValidationError("Cadastre um modelo de cálculo padrão em Parâmetros.")
    short_description = request_item.description[:180]
    composition = Composition.objects.create(company=request_item.company, name=short_description, calculation_model=model)
    CompositionItem.objects.create(company=request_item.company, composition=composition, name=short_description, description=request_item.description, quantity=1, unit="un")
    order = Order.objects.create(
        company=request_item.company,
        code=Sequence.next(request_item.company, "PED"),
        customer=request_item.customer,
        request=request_item,
        composition=composition,
        description=request_item.description,
        deadline=deadline,
        calculation_status="pending",
    )
    ProductionDemand.objects.create(
        company=request_item.company,
        code=order.code,
        origin="order",
        order=order,
        item_name=request_item.description,
        quantity=1,
        stage="art",
        deadline=deadline,
    )
    request_item.status = "ordered"
    request_item.save(update_fields=["status", "updated_at"])
    finish_once(record, order)
    return order


@transaction.atomic
def quote_to_order(quote, key, deadline=None):
    quote = Quote.objects.select_for_update().select_related("composition", "request", "customer").get(pk=quote.pk)
    record, created = begin_once(quote.company, key, "quote_to_order")
    if not created:
        return previous_result(record)
    if hasattr(quote, "order"):
        finish_once(record, quote.order)
        return quote.order
    if not quote.composition.is_calculated:
        raise ValidationError("Calcule o orçamento antes de convertê-lo em pedido.")
    if quote.manual_value is None or quote.manual_value < 0:
        raise ValidationError("Informe o valor final manual do orçamento antes de convertê-lo em pedido.")
    composition = clone_composition(quote.composition, f"Pedido de {quote.request.description}")
    order = Order.objects.create(
        company=quote.company,
        code=Sequence.next(quote.company, "PED"),
        customer=quote.customer,
        request=quote.request,
        quote=quote,
        composition=composition,
        description=quote.request.description,
        deadline=deadline,
        value=quote.manual_value,
        predicted_cost=composition.direct_cost,
        actual_cost=composition.direct_cost,
        calculation_status="completed",
        snapshot=composition.snapshot,
    )
    ProductionDemand.objects.create(
        company=quote.company,
        code=order.code,
        origin="order",
        order=order,
        item_name=order.description,
        quantity=1,
        stage="art",
        deadline=deadline,
    )
    quote.status = "converted"
    quote.request.status = "ordered"
    quote.save(update_fields=["status", "updated_at"])
    quote.request.save(update_fields=["status", "updated_at"])
    finish_once(record, order)
    return order


@transaction.atomic
def complete_order_calculation(order):
    order = Order.objects.select_for_update().select_related("composition").get(pk=order.pk)
    values = order.composition.recalculate()
    if not order.quote_id:
        order.value = values["final_price"]
    order.predicted_cost = values["direct_cost"]
    failures = order.demands.filter(active=True).values_list("failures__additional_cost", flat=True)
    order.actual_cost = money(order.predicted_cost + sum((value or Decimal("0") for value in failures), Decimal("0")))
    order.calculation_status = "completed"
    order.snapshot = order.composition.snapshot
    order.save(update_fields=["value", "predicted_cost", "actual_cost", "calculation_status", "snapshot", "updated_at"])
    return order


@transaction.atomic
def move_product_stock(product, quantity, movement_type, user=None, source=None, note=""):
    product = product.__class__.objects.select_for_update().get(pk=product.pk)
    quantity = to_decimal(quantity)
    product.current_stock += quantity
    product.save(update_fields=["current_stock", "updated_at"])
    return StockMovement.objects.create(
        company=product.company,
        product=product,
        movement_type=movement_type,
        quantity=quantity,
        balance_after=product.current_stock,
        source_type=source._meta.label if source else "",
        source_id=str(source.pk) if source else "",
        note=note,
        user=user,
    )


def composition_supply_requirements(composition, multiplier=1):
    requirements = {}
    multiplier = to_decimal(multiplier, "1")
    for item in composition.items.filter(active=True).prefetch_related("supplies__supply"):
        for use in item.supplies.filter(active=True):
            entry = requirements.setdefault(use.supply_id, {"supply": use.supply, "quantity": Decimal("0")})
            entry["quantity"] += use.quantity * item.quantity * multiplier
    return requirements


def reserve_composition_supplies(demand, composition, multiplier=1):
    if demand.reserved_supplies:
        return demand.reserved_supplies
    locked = []
    insufficient = []
    for supply_id, entry in composition_supply_requirements(composition, multiplier).items():
        supply = entry["supply"].__class__.objects.select_for_update().get(pk=supply_id)
        locked.append((supply, entry["quantity"]))
        if supply.available_stock < entry["quantity"]:
            insufficient.append(f"{supply.name}: disponível {supply.available_stock} {supply.unit}; necessário {entry['quantity']} {supply.unit}")
    if insufficient:
        existing = Alert.objects.filter(
            company=demand.company,
            source_type="erp.ProductionDemand",
            source_id=str(demand.pk),
            resolved_at__isnull=True,
        ).first()
        if existing:
            existing.message = " | ".join(insufficient)
            existing.save(update_fields=["message", "updated_at"])
        else:
            Alert.objects.create(
                company=demand.company,
                level="critical",
                title=f"Insumos insuficientes para {demand.code}",
                message=" | ".join(insufficient),
                source_type="erp.ProductionDemand",
                source_id=str(demand.pk),
            )
        raise ValidationError("Insumos extras insuficientes. Reponha ou ajuste o estoque antes de liberar a impressão.")
    reserved = {}
    for supply, quantity in locked:
        supply.reserved_stock += quantity
        supply.save(update_fields=["reserved_stock", "updated_at"])
        reserved[str(supply.pk)] = str(quantity)
    demand.reserved_supplies = reserved
    demand.save(update_fields=["reserved_supplies", "updated_at"])
    return reserved


def deduct_composition_supplies(composition, multiplier, source, user=None):
    for supply_id, entry in composition_supply_requirements(composition, multiplier).items():
        supply = entry["supply"].__class__.objects.select_for_update().get(pk=supply_id)
        used = entry["quantity"]
        supply.physical_stock -= used
        reserved = to_decimal(getattr(source, "reserved_supplies", {}).get(str(supply.pk), "0"))
        if reserved:
            supply.reserved_stock = max(Decimal("0"), supply.reserved_stock - reserved)
        supply.save(update_fields=["physical_stock", "reserved_stock", "updated_at"])
        MaterialMovement.objects.create(
            company=composition.company,
            movement_type="production",
            supply=supply,
            quantity=-used,
            source_type=source._meta.label,
            source_id=str(source.pk),
            note=f"Baixa na conclusão de {source}",
            user=user,
        )
        if supply.available_stock < 0:
            Alert.objects.create(
                company=composition.company,
                level="critical",
                title=f"Estoque negativo: {supply.name}",
                message=f"A conclusão consumiu {used} {supply.unit} e deixou disponível {supply.available_stock}. Faça um ajuste manual.",
                source_type=source._meta.label,
                source_id=str(source.pk),
            )


@transaction.atomic
def advance_demand(demand, next_stage, user=None):
    demand = ProductionDemand.objects.select_for_update().select_related("order__composition", "product__composition").get(pk=demand.pk)
    stages = ["art", "material", "queue", "printing", "ready"]
    if next_stage not in stages:
        raise ValidationError("Etapa de produção inválida.")
    if next_stage == demand.stage:
        return demand
    if stages.index(next_stage) < stages.index(demand.stage):
        raise ValidationError("Use um ajuste administrativo para retroceder uma produção.")
    if next_stage != demand.stage and stages.index(next_stage) != stages.index(demand.stage) + 1:
        raise ValidationError("Avance uma etapa por vez.")
    if next_stage in {"material", "queue"} and demand.order and demand.order.calculation_status != "completed":
        raise ValidationError("Conclua o cálculo do pedido antes de avançar para Aguardando impressão.")
    composition = demand.order.composition if demand.order else (demand.product.composition if demand.product else None)
    multiplier = Decimal("1") if demand.order else demand.quantity
    if next_stage == "queue" and composition:
        reserve_composition_supplies(demand, composition, multiplier)
    demand.stage = next_stage
    if next_stage == "ready":
        demand.ready_at = timezone.now()
        if demand.origin == "replenishment" and demand.product and not demand.completed_stock_movement:
            move_product_stock(demand.product, demand.quantity, "production", user, demand, "Entrada automática da reposição concluída")
            demand.completed_stock_movement = True
        if composition:
            deduct_composition_supplies(composition, multiplier, demand, user)
            demand.reserved_supplies = {}
    demand.save(update_fields=["stage", "reserved_supplies", "ready_at", "completed_stock_movement", "updated_at"])
    return demand


@transaction.atomic
def register_production_failure(demand, part, failure_percent, reason, notes="", key=None):
    demand = ProductionDemand.objects.select_for_update().select_related("order__composition", "product__composition").get(pk=demand.pk)
    operation, created = begin_once(demand.company, key, "production_failure")
    if not created:
        return previous_result(operation)
    composition = demand.order.composition if demand.order else (demand.product.composition if demand.product else None)
    if not composition or not composition.is_calculated:
        raise ValidationError("Conclua o cálculo da composição antes de registrar uma falha.")
    percent = to_decimal(failure_percent)
    if percent <= 0 or percent > 100:
        raise ValidationError("O percentual da falha deve ser maior que 0 e menor ou igual a 100%.")
    if not part or not composition.items.filter(parts=part, active=True, parts__active=True).exists():
        raise ValidationError("Selecione uma parte válida da composição.")
    snapshot = next((item for item in composition.snapshot.get("parts", []) if str(item.get("part_id")) == str(part.pk)), None)
    if not snapshot:
        snapshot = next((item for item in composition.snapshot.get("parts", []) if item.get("part") == part.name and item.get("item") == part.item.name), None)
    if not snapshot:
        raise ValidationError("A parte selecionada não consta no snapshot de custos. Recalcule a composição.")
    base_cost = to_decimal(snapshot.get("snapshot_cost")) if "snapshot_cost" in snapshot else to_decimal(snapshot.get("grams")) * to_decimal(snapshot.get("reference_cost_kg")) / Decimal("1000")
    additional_cost = money(base_cost * percent / Decimal("100"))
    failure = ProductionFailure.objects.create(
        company=demand.company,
        demand=demand,
        part=part,
        failure_percent=percent,
        reason=reason,
        notes=notes,
        additional_cost=additional_cost,
    )
    finish_once(operation, failure)
    if demand.order:
        total_failures = ProductionFailure.objects.filter(demand__order=demand.order, active=True).aggregate(total=Sum("additional_cost"))["total"] or Decimal("0")
        demand.order.actual_cost = money(demand.order.predicted_cost + total_failures)
        demand.order.save(update_fields=["actual_cost", "updated_at"])
    return failure


@transaction.atomic
def create_replenishment(product, quantity=None, user=None):
    product = product.__class__.objects.select_for_update().get(pk=product.pk)
    quantity = to_decimal(quantity) if quantity else product.suggested_replenishment
    if quantity <= 0:
        raise ValidationError("A quantidade da reposição deve ser maior que zero.")
    demand = ProductionDemand.objects.create(
        company=product.company,
        code=Sequence.next(product.company, "REP"),
        origin="replenishment",
        product=product,
        item_name=product.name,
        quantity=quantity,
        stage="material",
        printer=None,
    )
    return demand


@transaction.atomic
def create_sale(company, customer, method, cart, account, key, user=None):
    validate_company(company, customer, method, account)
    record, created = begin_once(company, key, "create_sale")
    if not created:
        return previous_result(record)
    normalized = []
    gross = cost = Decimal("0")
    for product, quantity in cart:
        validate_company(company, product)
        product = product.__class__.objects.select_for_update().get(pk=product.pk)
        quantity = to_decimal(quantity)
        if quantity <= 0:
            continue
        if product.current_stock < quantity:
            raise ValidationError(f"Estoque insuficiente para {product.name}.")
        line_total = product.current_price * quantity
        line_cost = product.current_cost * quantity
        normalized.append((product, quantity, line_total, line_cost))
        gross += line_total
        cost += line_cost
    if not normalized:
        raise ValidationError("Adicione ao menos um produto à venda.")
    fee = money(gross * method.fee_percent / Decimal("100"))
    net = money(gross - fee)
    entry = FinancialEntry.objects.create(
        company=company,
        code=Sequence.next(company, "FIN"),
        direction="in",
        description="Venda no PDV",
        category="Vendas",
        account=account,
        customer=customer,
        payment_method=method,
        gross_amount=money(gross),
        fee_amount=fee,
        net_amount=net,
        status="paid",
        paid_at=timezone.now(),
        source_type="erp.Sale",
        snapshot={"method": method.name, "fee_percent": str(method.fee_percent)},
    )
    sale = Sale.objects.create(
        company=company,
        code=Sequence.next(company, "VEN"),
        customer=customer,
        payment_method=method,
        gross_amount=money(gross),
        fee_amount=fee,
        net_amount=net,
        cost_amount=money(cost),
        profit_amount=money(net - cost),
        financial_entry=entry,
        snapshot={"method": method.name, "fee_percent": str(method.fee_percent)},
    )
    entry.source_id = str(sale.pk)
    entry.save(update_fields=["source_id", "updated_at"])
    for product, quantity, line_total, line_cost in normalized:
        SaleItem.objects.create(
            company=company,
            sale=sale,
            product=product,
            quantity=quantity,
            unit_price=product.current_price,
            unit_cost=product.current_cost,
            total=money(line_total),
            snapshot={"sku": product.sku, "name": product.name, "price": str(product.current_price), "cost": str(product.current_cost)},
        )
        move_product_stock(product, -quantity, "sale", user, sale, f"Venda {sale.code}")
    finish_once(record, sale)
    return sale


@transaction.atomic
def record_order_payment(order, method, account, amount=None, key=None, received=True, notes=""):
    order = Order.objects.select_for_update().get(pk=order.pk)
    validate_company(order.company, method, account)
    record, created = begin_once(order.company, key, "order_payment")
    if not created:
        return previous_result(record)
    gross = to_decimal(amount) if amount else order.balance
    if gross <= 0 or gross > order.balance:
        raise ValidationError("O recebimento deve ser maior que zero e não pode exceder o saldo.")
    fee = money(gross * method.fee_percent / Decimal("100"))
    net = money(gross - fee)
    entry = FinancialEntry.objects.create(
        company=order.company,
        code=Sequence.next(order.company, "FIN"),
        direction="in",
        description=f"Recebimento do pedido {order.code}",
        category="Recebimento de pedidos",
        account=account,
        customer=order.customer,
        payment_method=method,
        gross_amount=money(gross),
        fee_amount=fee,
        net_amount=net,
        due_date=timezone.localdate(),
        status="paid" if received else "pending",
        paid_at=timezone.now() if received else None,
        source_type="erp.Order",
        source_id=str(order.pk),
        notes=notes,
        snapshot={"order": order.code, "method": method.name, "fee_percent": str(method.fee_percent)},
    )
    payment = Payment.objects.create(
        company=order.company,
        order=order,
        customer=order.customer,
        financial_entry=entry,
        method=method,
        gross_amount=money(gross),
        fee_amount=fee,
        net_amount=net,
        status="received" if received else "pending",
        snapshot=entry.snapshot,
    )
    total = order.received
    order.financial_status = "paid" if total >= order.value else ("partial" if total > 0 else "pending")
    order.save(update_fields=["financial_status", "updated_at"])
    finish_once(record, payment)
    return payment


@transaction.atomic
def create_manual_entries(company, direction, description, category, amount, account, method, due_date, paid_now, installments=1, customer=None, supplier="", notes=""):
    validate_company(company, account, method, customer)
    total = money(to_decimal(amount))
    installments = max(1, int(installments or 1))
    base = money(total / installments)
    created = []
    allocated = Decimal("0")
    for index in range(installments):
        value = base if index < installments - 1 else money(total - allocated)
        allocated += value
        fee = money(value * (method.fee_percent if method and direction == "in" else Decimal("0")) / Decimal("100"))
        net = money(value - fee) if direction == "in" else value
        entry = FinancialEntry.objects.create(
            company=company,
            code=Sequence.next(company, "FIN"),
            direction=direction,
            description=description,
            category=category,
            account=account,
            customer=customer,
            supplier=supplier,
            payment_method=method,
            gross_amount=value,
            fee_amount=fee,
            net_amount=net,
            due_date=add_months(due_date, index),
            status="paid" if paid_now else "pending",
            paid_at=timezone.now() if paid_now else None,
            installment_number=index + 1,
            installments_total=installments,
            source_type="manual",
            notes=notes,
        )
        created.append(entry)
    return created


@transaction.atomic
def settle_financial_entry(entry):
    entry = FinancialEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.status == "cancelled":
        raise ValidationError("Lançamento cancelado não pode ser liquidado.")
    entry.status = "paid"
    entry.paid_at = timezone.now()
    entry.save(update_fields=["status", "paid_at", "updated_at"])
    return entry


@transaction.atomic
def open_roll(filament, user=None):
    filament = filament.__class__.objects.select_for_update().get(pk=filament.pk)
    if filament.closed_rolls < 1:
        raise ValidationError("Não há rolos fechados disponíveis.")
    filament.closed_rolls -= 1
    filament.open_rolls += 1
    filament.save(update_fields=["closed_rolls", "open_rolls", "updated_at"])
    MaterialMovement.objects.create(company=filament.company, movement_type="open_roll", filament=filament, quantity=1, note="Abertura manual de rolo", user=user)
    return filament


@transaction.atomic
def close_roll(filament, user=None):
    filament = filament.__class__.objects.select_for_update().get(pk=filament.pk)
    if filament.open_rolls < 1:
        raise ValidationError("Não há rolos em uso para finalizar.")
    filament.open_rolls -= 1
    filament.save(update_fields=["open_rolls", "updated_at"])
    MaterialMovement.objects.create(company=filament.company, movement_type="close_roll", filament=filament, quantity=-1, note="Finalização manual de rolo", user=user)
    return filament


@transaction.atomic
def complete_purchase(purchase, account, user=None):
    purchase = Purchase.objects.select_for_update().prefetch_related("items__filament__family", "items__supply").get(pk=purchase.pk)
    if purchase.completed_at:
        return purchase
    validate_company(purchase.company, account)
    total = Decimal("0")
    for item in purchase.items.all():
        total += item.total
        if item.filament:
            filament = item.filament.__class__.objects.select_for_update().get(pk=item.filament_id)
            rolls = int(item.quantity)
            prior_rolls = filament.family.filaments.aggregate(total=Sum("closed_rolls"))["total"] or 0
            prior_value = filament.family.weighted_cost_kg * Decimal(prior_rolls)
            kg_per_roll = Decimal(filament.nominal_weight_g) / Decimal("1000")
            new_cost_kg = item.unit_cost / kg_per_roll if kg_per_roll else item.unit_cost
            divisor = Decimal(prior_rolls + rolls)
            family = filament.family
            family.last_cost_kg = money(new_cost_kg)
            family.weighted_cost_kg = money((prior_value + new_cost_kg * rolls) / divisor) if divisor else money(new_cost_kg)
            family.save(update_fields=["last_cost_kg", "weighted_cost_kg", "updated_at"])
            family.refresh_reference_cost()
            filament.closed_rolls += rolls
            filament.unit_cost = item.unit_cost
            filament.save(update_fields=["closed_rolls", "unit_cost", "updated_at"])
            MaterialMovement.objects.create(company=purchase.company, movement_type="purchase", filament=filament, quantity=item.quantity, source_type="erp.Purchase", source_id=str(purchase.pk), note=purchase.code, user=user)
        else:
            supply = item.supply.__class__.objects.select_for_update().get(pk=item.supply_id)
            supply.physical_stock += item.quantity
            supply.unit_cost = item.unit_cost
            supply.save(update_fields=["physical_stock", "unit_cost", "updated_at"])
            MaterialMovement.objects.create(company=purchase.company, movement_type="purchase", supply=supply, quantity=item.quantity, source_type="erp.Purchase", source_id=str(purchase.pk), note=purchase.code, user=user)
    purchase.total = money(total)
    purchase.completed_at = timezone.now()
    purchase.save(update_fields=["total", "completed_at", "updated_at"])
    entries = create_manual_entries(
        purchase.company,
        "out",
        f"Compra {purchase.code} - {purchase.supplier}",
        "Compra de materiais",
        purchase.total,
        account,
        purchase.payment_method,
        purchase.first_due_date,
        False,
        purchase.installments,
        supplier=purchase.supplier,
        notes=purchase.notes,
    )
    for entry in entries:
        entry.source_type = "erp.Purchase"
        entry.source_id = str(purchase.pk)
        entry.save(update_fields=["source_type", "source_id", "updated_at"])
    return purchase


@transaction.atomic
def correct_purchase(purchase, quantity, unit_cost, reason, user=None):
    purchase = Purchase.objects.select_for_update().prefetch_related("items__filament__family", "items__supply").get(pk=purchase.pk)
    if not reason.strip():
        raise ValidationError("Informe o motivo da correção.")
    item = purchase.items.first()
    if not item:
        raise ValidationError("A compra não possui item para corrigir.")
    new_quantity = to_decimal(quantity)
    new_unit_cost = to_decimal(unit_cost)
    if new_quantity <= 0 or new_unit_cost < 0:
        raise ValidationError("Quantidade e custo devem ser válidos.")
    if item.filament_id and new_quantity != new_quantity.to_integral_value():
        raise ValidationError("Informe uma quantidade inteira de rolos.")
    old_quantity = item.quantity
    old_total = purchase.total
    new_line_total = money(new_quantity * new_unit_cost)
    new_total = money(old_total - item.total + new_line_total)
    delta_quantity = new_quantity - old_quantity
    if purchase.completed_at:
        if item.filament:
            filament = item.filament.__class__.objects.select_for_update().get(pk=item.filament_id)
            new_closed = filament.closed_rolls + int(delta_quantity)
            if new_closed < 0:
                raise ValidationError("A correção deixaria o estoque de rolos negativo.")
            before = filament.closed_rolls
            filament.closed_rolls = new_closed
            filament.unit_cost = new_unit_cost
            filament.save(update_fields=["closed_rolls", "unit_cost", "updated_at"])
            family = filament.family
            kg_per_roll = Decimal(filament.nominal_weight_g) / Decimal("1000")
            family.last_cost_kg = money(new_unit_cost / kg_per_roll) if kg_per_roll else new_unit_cost
            family.save(update_fields=["last_cost_kg", "updated_at"])
            family.refresh_reference_cost()
            MaterialMovement.objects.create(
                company=purchase.company, movement_type="adjustment", filament=filament, quantity=delta_quantity,
                source_type="erp.Purchase", source_id=str(purchase.pk), note=f"Correção {purchase.code}: {reason}", user=user,
                details={"before_quantity": str(old_quantity), "after_quantity": str(new_quantity), "stock_before": before, "stock_after": new_closed},
            )
        else:
            supply = item.supply.__class__.objects.select_for_update().get(pk=item.supply_id)
            new_stock = supply.physical_stock + delta_quantity
            if new_stock < 0:
                raise ValidationError("A correção deixaria o estoque físico negativo.")
            before = supply.physical_stock
            supply.physical_stock = new_stock
            supply.unit_cost = new_unit_cost
            supply.save(update_fields=["physical_stock", "unit_cost", "updated_at"])
            MaterialMovement.objects.create(
                company=purchase.company, movement_type="adjustment", supply=supply, quantity=delta_quantity,
                source_type="erp.Purchase", source_id=str(purchase.pk), note=f"Correção {purchase.code}: {reason}", user=user,
                details={"before_quantity": str(old_quantity), "after_quantity": str(new_quantity), "stock_before": str(before), "stock_after": str(new_stock), "reserved": str(supply.reserved_stock)},
            )
            if supply.available_stock < 0:
                Alert.objects.create(
                    company=purchase.company, level="critical", title=f"Correção abaixo da reserva: {supply.name}",
                    message=f"A correção da compra {purchase.code} manteve {supply.reserved_stock} reservados e deixou {supply.available_stock} disponíveis.",
                    source_type="erp.Purchase", source_id=str(purchase.pk),
                )
        difference = money(new_total - old_total)
        if difference:
            original_entries = FinancialEntry.objects.filter(source_type="erp.Purchase", source_id=str(purchase.pk), active=True)
            paid = original_entries.filter(status="paid").exists()
            amount = abs(difference)
            FinancialEntry.objects.create(
                company=purchase.company, code=Sequence.next(purchase.company, "FIN"),
                direction="out" if difference > 0 else "in",
                description=f"{'Correção' if difference > 0 else 'Estorno'} da compra {purchase.code}",
                category="Correção de compra", account=purchase.account, supplier=purchase.supplier,
                payment_method=purchase.payment_method, gross_amount=amount, fee_amount=0, net_amount=amount,
                due_date=timezone.localdate(), status="pending", paid_at=None,
                source_type="erp.PurchaseCorrection", source_id=str(purchase.pk), notes=reason,
                snapshot={"before": str(old_total), "after": str(new_total), "paid_installments_adjusted": paid},
            )
    item.quantity = new_quantity
    item.unit_cost = new_unit_cost
    item.total = new_line_total
    item.save(update_fields=["quantity", "unit_cost", "total", "updated_at"])
    purchase.total = new_total
    purchase.notes = (purchase.notes + f"\nCorreção: {reason}").strip()
    purchase.save(update_fields=["total", "notes", "updated_at"])
    return purchase


@transaction.atomic
def complete_shipment(shipment, user=None):
    shipment = ConsignmentShipment.objects.select_for_update().prefetch_related("items__product").get(pk=shipment.pk)
    if shipment.completed_at:
        return shipment
    for item in shipment.items.all():
        validate_company(shipment.company, item.product)
        product = item.product.__class__.objects.select_for_update().get(pk=item.product_id)
        if item.quantity <= 0 or item.reference_price < 0 or not 0 <= item.commission_percent <= 100:
            raise ValidationError("Confira quantidade, preço e comissão dos produtos da remessa.")
        if product.current_stock < item.quantity:
            raise ValidationError(f"Estoque insuficiente para {item.product.name}.")
        move_product_stock(item.product, -item.quantity, "consignment", user, shipment, f"Remessa {shipment.code}")
        balance, _ = ConsignmentBalance.objects.select_for_update().get_or_create(
            company=shipment.company,
            store=shipment.store,
            product=item.product,
            defaults={"quantity": 0, "reference_price": item.reference_price, "commission_percent": item.commission_percent},
        )
        balance.quantity += item.quantity
        balance.reference_price = item.reference_price
        balance.commission_percent = item.commission_percent
        balance.save()
    shipment.completed_at = timezone.now()
    shipment.save(update_fields=["completed_at", "updated_at"])
    return shipment


@transaction.atomic
def create_settlement(company, store, found, period_reference):
    validate_company(company, store)
    settlement = ConsignmentSettlement.objects.create(
        company=company,
        code=Sequence.next(company, "CON"),
        store=store,
        period_reference=period_reference,
    )
    has_divergence = False
    gross = commission = net = units = Decimal("0")
    balances = ConsignmentBalance.objects.filter(company=company, store=store, active=True).select_related("product")
    for balance in balances:
        expected = balance.quantity
        found_qty = to_decimal(found.get(str(balance.product_id), expected))
        if found_qty < 0:
            raise ValidationError("A quantidade encontrada não pode ser negativa.")
        divergence = found_qty > expected
        sold = Decimal("0") if divergence else expected - found_qty
        line_gross = sold * balance.reference_price
        line_commission = line_gross * balance.commission_percent / Decimal("100")
        line_net = line_gross - line_commission
        ConsignmentSettlementItem.objects.create(
            company=company,
            settlement=settlement,
            product=balance.product,
            expected_quantity=expected,
            found_quantity=found_qty,
            sold_quantity=sold,
            reference_price=balance.reference_price,
            commission_percent=balance.commission_percent,
            gross_amount=money(line_gross),
            commission_amount=money(line_commission),
            net_amount=money(line_net),
            divergence=divergence,
        )
        has_divergence = has_divergence or divergence
        gross += line_gross
        commission += line_commission
        net += line_net
        units += sold
    settlement.units_sold = units
    settlement.gross_amount = money(gross)
    settlement.commission_amount = money(commission)
    settlement.net_amount = money(net)
    settlement.status = "blocked" if has_divergence else "draft"
    settlement.save()
    return settlement


@transaction.atomic
def complete_settlement(settlement, account, method, user=None):
    settlement = ConsignmentSettlement.objects.select_for_update().prefetch_related("items__product").get(pk=settlement.pk)
    if settlement.status == "completed":
        return settlement
    if settlement.items.filter(divergence=True).exists():
        raise ValidationError("Resolva as divergências antes de concluir a prestação.")
    validate_company(settlement.company, account, method)
    for item in settlement.items.all():
        balance = ConsignmentBalance.objects.select_for_update().get(company=settlement.company, store=settlement.store, product=item.product)
        if balance.quantity != item.expected_quantity:
            raise ValidationError("O estoque consignado mudou após esta contagem. Faça uma nova prestação antes de concluir.")
        if item.disposition == "return" and item.found_quantity > 0:
            move_product_stock(item.product, item.found_quantity, "consignment_return", user, settlement, f"Retorno da prestação {settlement.code}")
            balance.quantity = Decimal("0")
        else:
            balance.quantity = item.found_quantity
        balance.save(update_fields=["quantity", "updated_at"])
    entry = FinancialEntry.objects.create(
        company=settlement.company,
        code=Sequence.next(settlement.company, "FIN"),
        direction="in",
        description=f"Consignação {settlement.store.name} - {settlement.period_reference}",
        category="Consignação",
        account=account,
        payment_method=method,
        gross_amount=settlement.gross_amount,
        fee_amount=settlement.commission_amount,
        net_amount=settlement.net_amount,
        status="paid",
        paid_at=timezone.now(),
        source_type="erp.ConsignmentSettlement",
        source_id=str(settlement.pk),
        snapshot={"commission": str(settlement.commission_amount), "store": settlement.store.name},
    )
    settlement.status = "completed"
    settlement.financial_entry = entry
    settlement.completed_at = timezone.now()
    settlement.save(update_fields=["status", "financial_entry", "completed_at", "updated_at"])
    return settlement
