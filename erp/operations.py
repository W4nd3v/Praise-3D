"""Leituras operacionais compartilhadas pelas telas e pelos atalhos."""
from collections import Counter
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from . import models as m


def record_url(label, pk):
    routes = {
        "erp.Order": f"/pedidos/{pk}/", "erp.Quote": f"/orcamentos/?selected={pk}",
        "erp.QuoteRequest": f"/solicitacoes/{pk}/", "erp.Customer": f"/clientes/{pk}/",
        "erp.ProductionDemand": f"/producao/?selected={pk}", "erp.Product": f"/estoque/?product={pk}",
        "erp.Sale": f"/vendas/{pk}/", "erp.Purchase": f"/compras/{pk}/",
        "erp.FinancialEntry": f"/financeiro/?entry={pk}", "erp.ConsignedStore": f"/consignacao/?store={pk}",
        "erp.Supply": f"/materiais/#supply-row-{pk}", "erp.Filament": f"/materiais/#filament-row-{pk}",
        "erp.ConsignmentShipment": f"/consignacao/?shipment={pk}",
        "erp.IssuedDocument": f"/documentos/{pk}.pdf",
        "erp.Printer": f"/parametros/#printers", "erp.MaterialFamily": "/parametros/#families",
        "erp.PaymentMethod": "/parametros/#payments", "erp.ProductCategory": "/parametros/#categories", "erp.ProductType": "/parametros/#types",
    }
    return routes.get(label, "")


def visible_records(model, company, archived=False):
    qs = model.objects.filter(company=company)
    if not archived:
        ids = m.ArchivedRecord.objects.filter(company=company, source_model=model._meta.label, archived=True).values_list("source_id", flat=True)
        qs = qs.exclude(pk__in=ids)
    return qs


def order_state(order):
    demands = [d for d in order.demands.all() if d.active]
    counts = Counter(d.stage for d in demands)
    if order.cancelled_at:
        key, label = "cancelled", "Cancelado"
    elif order.delivered_at:
        key, label = "delivered", "Entregue"
    elif counts["art"]:
        key, label = "art", "Aguardando arte"
    elif counts["material"]:
        key, label = "material", "Aguardando material"
    elif counts["ready"] and (counts["queue"] or counts["printing"]):
        key, label = "partial", "Aguardando produção"
    elif counts["queue"] or counts["printing"]:
        key, label = "production", "Em produção"
    elif demands and counts["ready"] == len(demands):
        key, label = "delivery", "Aguardando entrega"
    else:
        key, label = "pending", "Sem produção vinculada"
    return {"key": key, "label": label, "count": len(demands), "all_ready": bool(demands) and counts["ready"] == len(demands),
        "production_status": "Pronto" if demands and counts["ready"] == len(demands) else label,
        "summary": [{"stage": stage, "label": text, "count": counts[stage]} for stage, text in m.ProductionDemand.STAGES if counts[stage]]}


def demand_requirements(demand):
    from .services import composition_supply_requirements
    composition = demand.order.composition if demand.order_id else (demand.product.composition if demand.product_id else None)
    if not composition:
        return {}
    return composition_supply_requirements(composition, 1 if demand.order_id else demand.quantity, item_id=demand.composition_item_id)


def shortage_rows(demand):
    rows = []
    for pk, entry in demand_requirements(demand).items():
        supply = entry["supply"]
        own_reservation = Decimal(demand.reserved_supplies.get(str(pk), "0"))
        available = supply.available_stock + own_reservation
        needed = entry["quantity"]
        rows.append({"supply": supply, "needed": needed, "available": available,
            "missing": max(Decimal("0"), needed - available)})
    return rows


def refresh_material_alerts(company):
    for demand in operating_demands(company).filter(stage="material"):
        rows = shortage_rows(demand)
        missing = [row for row in rows if row["missing"] > 0]
        alerts = m.Alert.objects.filter(company=company, source_type="erp.ProductionDemand", source_id=str(demand.pk), title__startswith="Insumos insuficientes", resolved_at__isnull=True)
        if not missing:
            alerts.update(resolved_at=timezone.now())
        else:
            message = " | ".join(f"{r['supply'].name}: faltam {r['missing']} {r['supply'].unit}" for r in missing)
            current = alerts.first()
            if current:
                current.message = message
                current.save(update_fields=["message", "updated_at"])
            else:
                m.Alert.objects.create(company=company, level="critical", title=f"Insumos insuficientes para {demand.code}", message=message, source_type="erp.ProductionDemand", source_id=str(demand.pk))


def demand_priority(demand):
    return demand.order.priority_level if demand.order_id else ("priority" if demand.priority else "normal")


def sort_demands(demands, sort="smart"):
    far = timezone.localdate().replace(year=9999, month=12, day=31)
    rank = {"urgent": 0, "priority": 1, "normal": 2}
    def key(d):
        due = d.deadline or (d.order.deadline if d.order_id else None)
        priority = rank[demand_priority(d)]
        if sort == "deadline": return (due or far, d.created_at, d.pk)
        if sort == "priority": return (priority, due or far, d.created_at, d.pk)
        if sort == "customer": return ((d.order.customer.name if d.order_id else "Estoque central").casefold(), d.pk)
        if sort == "stage": return ([s for s, _ in m.ProductionDemand.STAGES].index(d.stage), d.pk)
        if sort == "code": return (d.code, d.pk)
        return (0 if due and due < timezone.localdate() else 1, priority, due or far, d.created_at, d.pk)
    return sorted(demands, key=key)


def action(label, url, record=None, category="", urgent=False):
    return {"label": label, "url": url, "record": record, "category": category, "urgent": urgent}


def next_order_action(order):
    url = record_url("erp.Order", order.pk)
    state = order_state(order)
    if order.cancelled_at: return action("Ver cancelamento", url, order)
    if order.calculation_status == "pending": return action("Concluir cálculo", f"/composicao/{order.composition_id}/", order, "Cálculo")
    if not order.delivered_at:
        if state["key"] == "art": return action("Finalizar arte", f"/producao/?order={order.pk}&stage=art", order, "Arte")
        if state["key"] == "delivery": return action("Entregar pedido", url, order, "Entrega")
        return action("Ver produção", f"/producao/?order={order.pk}", order, "Produção")
    if order.balance > 0: return action("Registrar recebimento", f"{url}?receive=1", order, "Financeiro")
    return action("Ver pedido", url, order)


def next_demand_action(demand):
    url = record_url("erp.ProductionDemand", demand.pk)
    if demand.order_id and demand.order.calculation_status == "pending":
        return action("Concluir cálculo", f"/composicao/{demand.order.composition_id}/", demand)
    if demand.stage == "material":
        missing = any(r["missing"] for r in shortage_rows(demand))
        return action("Comprar insumo" if missing else "Liberar para impressão", f"/producao/{demand.pk}/comprar-faltantes/" if missing else url, demand)
    return action({"art": "Finalizar arte", "queue": "Iniciar impressão", "printing": "Finalizar impressão", "ready": "Ver pedido"}.get(demand.stage, "Ver produção"), record_url("erp.Order", demand.order_id) if demand.stage == "ready" and demand.order_id else url, demand)


def annotate_order(order):
    order.state = order_state(order)
    order.next_action = next_order_action(order)
    return order


def operating_demands(company):
    return visible_records(m.ProductionDemand, company).filter(active=True).filter(
        Q(order__isnull=True) | Q(order__active=True, order__cancelled_at__isnull=True, order__delivered_at__isnull=True)
    ).select_related("order__customer", "order__composition", "product__composition", "printer", "composition_item").prefetch_related("order__demands")


def stock_distribution(product):
    rows = []
    for balance in product.consignment_balances.filter(quantity__gt=0).select_related("store"):
        shipment = m.ConsignmentShipmentItem.objects.filter(product=product, shipment__store=balance.store, shipment__completed_at__isnull=False, shipment__cancelled_at__isnull=True).select_related("shipment").order_by("-shipment__shipment_date", "-pk").first()
        settlement = m.ConsignmentSettlementItem.objects.filter(product=product, settlement__store=balance.store, settlement__status="completed").select_related("settlement").order_by("-settlement__settlement_date", "-pk").first()
        rows.append({"balance": balance, "shipment": shipment.shipment if shipment else None, "settlement": settlement.settlement if settlement else None})
    return rows


def dashboard_operations(company, reminders):
    orders = [annotate_order(o) for o in visible_records(m.Order, company).filter(active=True, cancelled_at__isnull=True).select_related("customer", "composition").prefetch_related("demands")]
    demands = list(operating_demands(company).exclude(stage="ready"))
    arts = [o for o in orders if not o.delivered_at and o.state["key"] == "art"]
    calc = [o for o in orders if o.calculation_status == "pending" and not o.delivered_at]
    shortages = [d for d in demands if d.stage == "material" and any(row["missing"] for row in shortage_rows(d))]
    ready = [o for o in orders if not o.delivered_at and o.state["all_ready"]]
    late = [o for o in orders if o.is_overdue]
    quotes = visible_records(m.Quote, company).filter(active=True, status__in=["sent", "waiting"]).select_related("customer")
    overdue_payments = m.FinancialEntry.objects.filter(company=company, active=True, status="pending", due_date__lt=timezone.localdate())
    pending = [
        ("Artes pendentes", len(arts), "/pedidos/?operation=art"),
        ("Cálculos pendentes", len(calc), "/pedidos/?calculation=pending"),
        ("Insumos faltantes", len(shortages), "/producao/?stage=material"),
        ("Orçamentos sem retorno", quotes.count(), "/orcamentos/?status=waiting"),
        ("Pedidos atrasados", len(late), "/pedidos/?deadline=overdue"),
        ("Pagamentos vencidos", overdue_payments.count(), "/financeiro/?overdue=1"),
        ("Entregas pendentes", len(ready), "/pedidos/?operation=delivery"),
        ("Lembretes vencidos", len(reminders), "/solicitacoes/?reminders=due"),
    ]
    actions = [action("Atender lembrete", f"/solicitacoes/{r.request_id}/", r.request, "Lembrete", True) for r in reminders]
    actions += [next_order_action(o) for o in sorted(orders, key=lambda o: (not o.is_overdue, {"urgent": 0, "priority": 1, "normal": 2}[o.priority_level], o.created_at)) if not o.delivered_at or o.balance > 0]
    actions += [next_demand_action(d) for d in shortages]
    actions += [action("Registrar resposta", f"/orcamentos/?selected={q.pk}", q, "Orçamento") for q in quotes[:5]]
    actions += [action("Cobrar cliente" if e.direction == "in" else "Pagar parcela", f"/financeiro/?entry={e.pk}", e, "Financeiro", True) for e in overdue_payments[:5]]
    for r in visible_records(m.QuoteRequest, company).filter(active=True, status__in=["new", "analysis", "waiting"])[:5]:
        actions.append(action("Entrar em contato" if r.status == "waiting" else "Criar orçamento", record_url("erp.QuoteRequest", r.pk), r, "Solicitação"))
    for q in visible_records(m.Quote, company).filter(active=True, status__in=["draft", "approved"])[:5]:
        actions.append(action("Converter em pedido" if q.status == "approved" else "Enviar ao cliente", record_url("erp.Quote", q.pk), q, "Orçamento"))
    from django.db.models import F
    for p in m.Product.objects.filter(company=company, active=True, current_stock__lte=F("minimum_stock"))[:5]:
        actions.append(action("Criar reposição", record_url("erp.Product", p.pk), p, "Estoque"))
    return {"pending_categories": pending, "next_actions": actions[:30], "ready_orders": ready}
