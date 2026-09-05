from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
from urllib.parse import urlencode
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.template.loader import render_to_string

from . import models as m
from . import operations as op
from .activity import log_event
from .reminders import refresh_due, visible_reminders, snooze_reminder, finish_reminder, PENDING
from .services import begin_once, finish_once, previous_result, complete_purchase, to_decimal


def role_for(request):
    return "admin" if request.user.is_superuser else getattr(getattr(request, "membership", None), "role", "viewer")


def require_role(request, area="operation"):
    allowed = {"admin", "operator", "finance"} if area == "reminder" else ({"admin", "finance"} if area == "finance" else {"admin", "operator"})
    if role_for(request) not in allowed:
        raise PermissionDenied("Seu perfil não permite esta ação.")


def tenant(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.company:
            raise PermissionDenied
        if request.method == "POST" and role_for(request) == "viewer":
            raise PermissionDenied("Perfil somente leitura.")
        return view(request, *args, **kwargs)
    return wrapped


def safe_back(request, default):
    value = request.POST.get("next") or request.GET.get("return_to", "")
    return value if value and url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()}, require_https=request.is_secure()) and value.startswith("/") else default


def common(request):
    from .views import common_context
    return common_context(request.company)


def timeline(query, category=""):
    groups = {"orders": "order.", "quotes": "quote.", "sales": "sale.", "payments": "payment.", "requests": "request.", "reminders": "reminder."}
    if category in groups:
        query = query.filter(kind__startswith=groups[category])
    return query.select_related("user").order_by("-happened_at", "-pk")


@tenant
def request_detail(request, pk):
    item = get_object_or_404(m.QuoteRequest.objects.select_related("customer"), pk=pk, company=request.company)
    if request.method == "POST":
        require_role(request, "reminder")
        try:
            with transaction.atomic():
                item = m.QuoteRequest.objects.select_for_update().get(pk=pk, company=request.company)
                if request.POST.get("action") == "reminder_complete":
                    reminder = get_object_or_404(visible_reminders(request), request=item)
                    finish_reminder(reminder, request.user)
                    messages.success(request, "Tarefa realizada. O lembrete foi concluído.")
                elif request.POST.get("action") == "reminder_schedule":
                    when = timezone.make_aware(datetime.fromisoformat(request.POST.get("when", "")))
                    purpose = request.POST.get("purpose", "manual")
                    if purpose not in dict(m.RequestReminder.PURPOSES):
                        raise ValidationError("Finalidade inválida.")
                    existing = m.RequestReminder.objects.filter(request=item).first()
                    if existing:
                        get_object_or_404(visible_reminders(request), pk=existing.pk)
                        if existing.status not in PENDING:
                            raise ValidationError("Este lembrete já foi encerrado; seu histórico é preservado.")
                        snooze_reminder(existing, when, request.POST.get("version"), request.user)
                    else:
                        item.reminder_at = when
                        item.save(update_fields=["reminder_at", "updated_at"])
                        item.reminder.purpose = purpose
                        item.reminder.save(update_fields=["purpose"])
                    messages.success(request, "Lembrete agendado.")
                elif request.POST.get("action") == "edit":
                    item.description = request.POST.get("description", "").strip()
                    if not item.description: raise ValidationError("Informe a descrição.")
                    item.notes = request.POST.get("notes", "")
                    item.save(update_fields=["description", "notes", "updated_at"])
                    log_event(item, "request.edited", "Dados da solicitação atualizados")
                    messages.success(request, "Solicitação atualizada.")
        except (ValueError, ValidationError) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else "Informe data e horário válidos.")
        return redirect("request_detail", pk=pk)
    refresh_due(request.company)
    reminder = visible_reminders(request).filter(request=item).first()
    return render(request, "erp/request_detail.html", {"title": item.code, "item": item, "reminder": reminder,
        "events": timeline(item.activity_events.all()), "idempotency_key": uuid.uuid4(), "purposes": m.RequestReminder.PURPOSES})


@tenant
def reminder_feed(request):
    refresh_due(request.company)
    due = list(visible_reminders(request).filter(status="due", scheduled_at__lte=timezone.now()))
    html = render_to_string("erp/partials/reminder_panel.html", {"due_reminders": due}, request=request)
    return JsonResponse({"count": len(due), "html": html, "request_ids": [r.request_id for r in due], "server_time": timezone.now().isoformat()})


@require_POST
@tenant
def reminder_snooze(request, pk):
    require_role(request, "reminder")
    reminder = get_object_or_404(visible_reminders(request), pk=pk)
    try:
        choice = request.POST.get("delay", "15")
        now = timezone.localtime()
        if choice == "custom":
            until = timezone.make_aware(datetime.fromisoformat(request.POST.get("when", "")))
        elif choice == "tomorrow":
            until = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        elif choice in {"5", "15", "30", "60", "120"}:
            until = now + timedelta(minutes=int(choice))
        else:
            raise ValidationError("Escolha um adiamento válido.")
        snooze_reminder(reminder, until, request.POST.get("version"), request.user)
    except (ValidationError, ValueError) as exc:
        error = "; ".join(exc.messages) if hasattr(exc, "messages") else "Informe data e horário válidos."
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"error": error}, status=400)
        messages.error(request, error)
    else:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest": return JsonResponse({"ok": True})
        messages.success(request, "Lembrete adiado.")
    return redirect(safe_back(request, f"/solicitacoes/{reminder.request_id}/"))


@tenant
def orders_view(request, pk=None):
    orders = op.visible_records(m.Order, request.company, request.GET.get("archived") == "1").filter(active=True).select_related("customer", "composition", "request", "quote").prefetch_related("demands__printer", "composition__items")
    for param, field in [("priority", "priority_level"), ("payment", "financial_status"), ("calculation", "calculation_status")]:
        if request.GET.get(param): orders = orders.filter(**{field: request.GET[param]})
    if request.GET.get("customer", "").isdigit(): orders = orders.filter(customer_id=request.GET["customer"])
    if request.GET.get("q"): orders = orders.filter(Q(code__icontains=request.GET["q"]) | Q(customer__name__icontains=request.GET["q"]) | Q(description__icontains=request.GET["q"]))
    records = [op.annotate_order(o) for o in orders]
    if request.GET.get("operation"): records = [o for o in records if o.state["key"] == request.GET["operation"]]
    if request.GET.get("deadline") == "overdue": records = [o for o in records if o.is_overdue]
    if request.GET.get("deadline") == "today": records = [o for o in records if o.deadline == timezone.localdate() and not o.delivered_at]
    selected_id = str(pk or request.GET.get("selected", ""))
    selected = None
    if selected_id.isdigit():
        selected = op.annotate_order(get_object_or_404(m.Order.objects.select_related("customer", "composition", "quote", "request").prefetch_related("demands__printer", "composition__items"), pk=selected_id, company=request.company))
    elif records: selected = records[0]
    events = m.ActivityEvent.objects.none()
    if selected:
        events = m.ActivityEvent.objects.filter(company=request.company).filter(Q(order=selected) | Q(request_id=selected.request_id) if selected.request_id else Q(order=selected))
        selected.receivables = m.FinancialEntry.objects.filter(company=request.company, source_type="erp.Order", source_id=str(selected.pk), active=True).exclude(status="cancelled")
    context = common(request)
    context.update({"title": selected.code if pk and selected else "Pedidos", "orders_list": records, "selected": selected,
        "events": timeline(events), "idempotency_key": uuid.uuid4(), "priorities": m.Order.PRIORITIES,
        "filter_suffix": urlencode({k:v for k,v in request.GET.items() if k not in {"selected", "receive"}}),
        "show_payment": request.GET.get("receive") == "1"})
    return render(request, "erp/orders.html", context)


@require_POST
@tenant
def order_update(request, pk):
    require_role(request)
    order = get_object_or_404(m.Order, pk=pk, company=request.company)
    try:
        with transaction.atomic():
            order = get_object_or_404(m.Order.objects.select_for_update(), pk=pk, company=request.company)
            if order.cancelled_at or order.delivered_at: raise ValidationError("Pedido encerrado não pode ser alterado.")
            priority = request.POST.get("priority", "normal")
            if priority not in dict(m.Order.PRIORITIES): raise ValidationError("Prioridade inválida.")
            order.priority_level = priority
            order.priority = priority != "normal"
            order.deadline = datetime.strptime(request.POST["deadline"], "%Y-%m-%d").date() if request.POST.get("deadline") else None
            order.save(update_fields=["priority_level", "priority", "deadline", "updated_at"])
            for demand in order.demands.filter(active=True):
                demand.deadline = order.deadline
                demand.priority = order.priority
                demand.save(update_fields=["deadline", "priority", "updated_at"])
            from .activity import log_event
            log_event(order, "order.schedule.updated", "Prioridade e prazo do pedido atualizados", request.user,
                details={"priority": priority, "deadline": order.deadline.isoformat() if order.deadline else None})
            messages.success(request, "Prioridade e prazo atualizados.")
    except (ValidationError, ValueError) as exc:
        error = "; ".join(exc.messages) if hasattr(exc, "messages") else "Data inválida."
        messages.error(request, error)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": error}, status=400)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        order.refresh_from_db()
        return JsonResponse({"ok": True, "priority": order.get_priority_level_display(), "deadline": order.deadline.strftime("%d/%m/%Y") if order.deadline else "Sem prazo"})
    return redirect("order_detail", pk=pk)


@tenant
def production_view(request):
    demands = op.operating_demands(request.company)
    if request.GET.get("order", "").isdigit(): demands = demands.filter(order_id=request.GET["order"])
    if request.GET.get("origin") in {"order", "replenishment"}: demands = demands.filter(origin=request.GET["origin"])
    if request.GET.get("printer", "").isdigit(): demands = demands.filter(printer_id=request.GET["printer"])
    if request.GET.get("q"): demands = demands.filter(Q(code__icontains=request.GET["q"]) | Q(item_name__icontains=request.GET["q"]) | Q(order__customer__name__icontains=request.GET["q"]))
    all_records = list(demands)
    selected_id = request.GET.get("selected", "")
    selected = next((d for d in all_records if str(d.pk) == selected_id), None)
    if selected_id.isdigit() and not selected:
        selected = get_object_or_404(m.ProductionDemand.objects.select_related("order__customer", "order__composition", "product__composition"), pk=selected_id, company=request.company)
        if request.GET.get("order") and str(selected.order_id) != request.GET["order"]: selected = None
    records = [d for d in all_records if d.stage != "ready"]
    if request.GET.get("stage") in dict(m.ProductionDemand.STAGES): records = [d for d in records if d.stage == request.GET["stage"]]
    if request.GET.get("priority"): records = [d for d in records if op.demand_priority(d) == request.GET["priority"]]
    if request.GET.get("deadline") == "overdue": records = [d for d in records if d.deadline and d.deadline < timezone.localdate()]
    records = op.sort_demands(records, request.GET.get("sort", "smart"))
    selected = selected or (records[0] if records else None)
    parts, shortages = [], []
    for d in records:
        d.next_action = op.next_demand_action(d)
        d.priority_label = dict(m.Order.PRIORITIES)[op.demand_priority(d)]
    if selected:
        composition = selected.order.composition if selected.order_id else (selected.product.composition if selected.product_id else None)
        if composition:
            parts = m.ManufacturingPart.objects.filter(item__composition=composition, item__active=True, active=True).select_related("item")
            if selected.composition_item_id: parts = parts.filter(item_id=selected.composition_item_id)
        shortages = op.shortage_rows(selected)
    context = common(request)
    completed_order = None
    if request.GET.get("completed", "").isdigit():
        completed_order = m.Order.objects.filter(company=request.company, pk=request.GET["completed"], cancelled_at__isnull=True).prefetch_related("demands").first()
        if completed_order and not op.order_state(completed_order)["all_ready"]: completed_order = None
    context.update({"title": "Produção", "demands": records, "selected": selected, "selected_parts": parts,
        "shortages": shortages, "has_shortages": any(r["missing"] for r in shortages),
        "ready_demands": [d for d in all_records if d.stage == "ready"], "stages": m.ProductionDemand.STAGES,
        "priorities": m.Order.PRIORITIES, "idempotency_key": uuid.uuid4(), "completed_order": completed_order,
        "filter_suffix": urlencode({k:v for k,v in request.GET.items() if k not in {"selected", "completed"}})})
    return render(request, "erp/production.html", context)


@require_POST
@tenant
def production_assign(request, pk):
    require_role(request)
    with transaction.atomic():
        demand = get_object_or_404(m.ProductionDemand.objects.select_for_update(), pk=pk, company=request.company, active=True)
        if demand.order_id and (demand.order.cancelled_at or demand.order.delivered_at):
            raise PermissionDenied("Pedido encerrado.")
        ident = request.POST.get("printer", "")
        demand.printer = get_object_or_404(m.Printer, pk=ident, company=request.company, active=True) if ident.isdigit() else None
        demand.save(update_fields=["printer", "updated_at"])
    return redirect(f"/producao/?selected={pk}")


@tenant
def customer_360(request, pk):
    customer = get_object_or_404(m.Customer, pk=pk, company=request.company)
    if request.method == "POST":
        require_role(request, "reminder")
        for field in ["name", "legal_name", "document", "phone", "whatsapp", "instagram", "email", "city", "notes"]:
            setattr(customer, field, request.POST.get(field, "").strip())
        customer.state = request.POST.get("state", "").strip().upper()
        if customer.name:
            customer.save()
            log_event(customer, "customer.edited", "Cadastro do cliente atualizado")
            messages.success(request, "Cliente atualizado.")
        return redirect("customer_detail", pk=pk)
    orders = [op.annotate_order(o) for o in customer.orders.select_related("composition").prefetch_related("demands")]
    sales = list(customer.sales.select_related("payment_method", "financial_entry").prefetch_related("items__product"))
    purchases = [{"date": o.created_at, "origin": "Pedido", "code": o.code, "summary": o.description, "amount": o.value,
        "payment": o.get_financial_status_display(), "status": o.state["label"], "url": f"/pedidos/{o.pk}/"} for o in orders]
    purchases += [{"date": s.created_at, "origin": "PDV", "code": s.code, "summary": ", ".join(i.snapshot.get("name", i.product.name) for i in s.items.all()), "amount": s.gross_amount,
        "payment": s.payment_method.name, "status": "Cancelada" if s.cancelled_at else "Concluída", "url": f"/vendas/{s.pk}/"} for s in sales]
    purchases.sort(key=lambda row: row["date"], reverse=True)
    valid = [p for p in purchases if p["status"] not in {"Cancelada", "Cancelado"}]
    pending_orders = [o for o in orders if not o.cancelled_at and o.balance > 0]
    for order in pending_orders:
        order.next_due = customer.financial_entries.filter(source_type="erp.Order", source_id=str(order.pk), status="pending").order_by("due_date").values_list("due_date", flat=True).first()
    return render(request, "erp/customer_detail.html", {"title": customer.name, "customer": customer, "orders": orders,
        "quotes": customer.quotes.select_related("composition").all(), "purchases": purchases,
        "open_orders": sum(1 for o in orders if not o.delivered_at and not o.cancelled_at),
        "last_purchase": valid[0]["date"] if valid else None, "average_ticket": sum((p["amount"] for p in valid), Decimal("0")) / len(valid) if valid else 0,
        "pending_orders": pending_orders, "pending_entries": customer.financial_entries.filter(active=True, status="pending"),
        "payments": customer.payments.select_related("method", "order").all(),
        "events": timeline(customer.activity_events.all(), request.GET.get("history", "")),
        "back_url": safe_back(request, "/clientes/"), "active_tab": request.GET.get("tab", "overview")})


@tenant
def sale_detail(request, pk):
    sale = get_object_or_404(m.Sale.objects.select_related("customer", "payment_method", "financial_entry").prefetch_related("items__product"), pk=pk, company=request.company)
    return render(request, "erp/sale_detail.html", {"title": sale.code, "sale": sale, "events": timeline(m.ActivityEvent.objects.filter(company=request.company, source_model="erp.Sale", source_id=pk))})


@tenant
def purchase_detail(request, pk):
    purchase = get_object_or_404(m.Purchase.objects.select_related("source_demand", "payment_method", "account").prefetch_related("items__supply", "items__filament"), pk=pk, company=request.company)
    if request.method == "POST":
        require_role(request)
        from .services import correct_purchase
        try:
            if request.POST.get("action") == "confirm":
                complete_purchase(purchase, purchase.account, request.user)
            else:
                line = get_object_or_404(m.PurchaseItem, pk=request.POST.get("item_id"), purchase=purchase, company=request.company)
                correct_purchase(purchase, request.POST.get("quantity"), request.POST.get("unit_cost"), request.POST.get("reason", ""), request.user, item_id=line.pk)
            messages.success(request, "Compra atualizada com histórico.")
        except ValidationError as exc: messages.error(request, "; ".join(exc.messages))
        return redirect("purchase_detail", pk=pk)
    return render(request, "erp/purchase_detail.html", {"title": purchase.code, "purchase": purchase,
        "entries": m.FinancialEntry.objects.filter(company=request.company, source_type__in=["erp.Purchase", "erp.PurchaseCorrection"], source_id=str(pk)),
        "events": timeline(m.ActivityEvent.objects.filter(company=request.company, source_model="erp.Purchase", source_id=pk))})


@tenant
def purchase_shortages(request, pk):
    demand = get_object_or_404(m.ProductionDemand.objects.select_related("order__composition", "product__composition"), pk=pk, company=request.company, active=True)
    rows = [r for r in op.shortage_rows(demand) if r["missing"] > 0]
    if request.method == "POST":
        require_role(request)
        try:
            with transaction.atomic():
                if demand.order_id and (demand.order.cancelled_at or demand.order.delivered_at): raise ValidationError("Pedido encerrado.")
                operation, created = begin_once(request.company, request.POST.get("idempotency_key"), "purchase_shortages")
                if not created:
                    return redirect("purchase_detail", pk=previous_result(operation).pk)
                method = get_object_or_404(m.PaymentMethod, pk=request.POST.get("payment_method"), company=request.company, active=True)
                account = m.FinancialAccount.objects.filter(
                    pk=request.POST.get("account"), company=request.company, active=True
                ).first() if request.POST.get("account") else None
                supplier = request.POST.get("supplier", "").strip()
                if not supplier: raise ValidationError("Informe o fornecedor.")
                if not 1 <= int(request.POST.get("installments") or 1) <= 120: raise ValidationError("Parcelas devem estar entre 1 e 120.")
                ids = request.POST.getlist("supply")
                quantities, costs = request.POST.getlist("quantity"), request.POST.getlist("unit_cost")
                allowed = set(op.demand_requirements(demand))
                if not ids or len(ids) != len(set(ids)) or len(ids) != len(quantities) or len(ids) != len(costs): raise ValidationError("Confira os itens da compra.")
                purchase = m.Purchase.objects.create(company=request.company, code=m.Sequence.next(request.company, "COM"),
                    supplier=supplier, payment_method=method, account=account, source_demand=demand,
                    installments=max(1, int(request.POST.get("installments") or 1)),
                    first_due_date=datetime.strptime(request.POST["first_due_date"], "%Y-%m-%d").date(),
                    status="pending", purchase_type="supply")
                total = Decimal("0")
                for ident, qty, cost in zip(ids, quantities, costs):
                    if not ident.isdigit() or int(ident) not in allowed: raise ValidationError("Insumo não pertence à demanda.")
                    supply = get_object_or_404(m.Supply, pk=ident, company=request.company, active=True)
                    qty, cost = to_decimal(qty), to_decimal(cost)
                    if not qty.is_finite() or not cost.is_finite() or qty <= 0 or cost < 0: raise ValidationError("Quantidade e custo inválidos.")
                    line = m.money(qty * cost)
                    m.PurchaseItem.objects.create(company=request.company, purchase=purchase, supply=supply, quantity=qty, unit_cost=cost, total=line)
                    total += line
                purchase.total = total
                purchase.save(update_fields=["total", "updated_at"])
                if request.POST.get("confirm") == "on": complete_purchase(purchase, account, request.user)
                finish_once(operation, purchase)
                messages.success(request, "Compra registrada. A disponibilidade foi reavaliada; a produção só avança após sua confirmação.")
                return redirect("purchase_detail", pk=purchase.pk)
        except (ValidationError, ValueError, KeyError) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else "Confira os campos da compra.")
    context = common(request)
    context.update({"title": "Comprar insumos faltantes", "demand": demand, "rows": rows, "today": timezone.localdate(), "idempotency_key": uuid.uuid4()})
    return render(request, "erp/purchase_shortages.html", context)


@tenant
def global_search(request):
    q = request.GET.get("q", "").strip()[:120]
    groups = []
    if q:
        searches = [
            ("Pedidos", m.Order, Q(code__icontains=q) | Q(customer__name__icontains=q)),
            ("Orçamentos", m.Quote, Q(code__icontains=q)), ("Solicitações", m.QuoteRequest, Q(code__icontains=q)),
            ("Clientes", m.Customer, Q(name__icontains=q) | Q(phone__icontains=q) | Q(whatsapp__icontains=q)),
            ("Produtos", m.Product, Q(name__icontains=q) | Q(sku__icontains=q)),
            ("Compras", m.Purchase, Q(code__icontains=q)), ("Insumos", m.Supply, Q(name__icontains=q)),
            ("Filamentos", m.Filament, Q(family__name__icontains=q) | Q(color__icontains=q)),
            ("Estabelecimentos", m.ConsignedStore, Q(name__icontains=q)),
        ]
        for label, model, condition in searches:
            found = model.objects.filter(condition, company=request.company)[:12]
            results = [{"label": getattr(obj, "code", None) or getattr(obj, "name", None) or str(obj), "url": op.record_url(model._meta.label, obj.pk)} for obj in found]
            if results: groups.append({"label": label, "results": results})
    if request.GET.get("format") == "json": return JsonResponse({"groups": groups})
    return render(request, "erp/search.html", {"title": "Busca global", "query": q, "groups": groups})


@tenant
def record_manage(request, kind, pk):
    catalog = {c.__name__: c for c in [m.Customer, m.Product, m.Supply, m.Filament, m.ConsignedStore, m.Printer, m.MaterialFamily, m.PaymentMethod, m.FinancialAccount, m.ProductCategory, m.ProductType]}
    operations = {c.__name__: c for c in [m.Order, m.Quote, m.QuoteRequest, m.Sale, m.Purchase, m.ConsignmentShipment, m.FinancialEntry, m.ProductionDemand]}
    model = (catalog | operations).get(kind)
    if not model: raise PermissionDenied
    obj = get_object_or_404(model, pk=pk, company=request.company)
    if request.method == "POST":
        from .lifecycle import cancel_record, set_record_active, archive_record, delete_or_inactivate
        require_role(request, "finance" if kind in {"FinancialEntry", "Sale", "Purchase"} else "operation")
        try:
            with transaction.atomic():
                action, reason = request.POST.get("action"), request.POST.get("reason", "").strip()
                if not reason: raise ValidationError("Informe o motivo.")
                deleted = False
                if action == "cancel" and kind in operations: cancel_record(obj, reason, request.user)
                elif action == "delete" and kind in catalog: deleted = delete_or_inactivate(obj, reason, request.user)
                elif action in {"activate", "deactivate"} and kind in catalog: set_record_active(obj, action == "activate", request.user)
                elif action in {"archive", "unarchive"}: archive_record(obj, action == "archive", request.user)
                else: raise ValidationError("Ação inválida.")
                if not deleted:
                    log_event(obj, "record.reason", reason, request.user)
            if deleted:
                messages.success(request, "Cadastro sem movimentações excluído definitivamente; a auditoria da ação foi preservada.")
                return redirect(f"/registros/?kind={kind}&status=all")
            messages.success(request, "Operação registrada. O histórico e eventuais estornos foram preservados.")
        except ValidationError as exc: messages.error(request, "; ".join(exc.messages))
        return redirect("record_manage", kind=kind, pk=pk)
    return render(request, "erp/record_manage.html", {"title": "Gerenciar registro", "obj": obj, "kind": kind, "label": kind,
        "can_cancel": kind in operations and kind != "ProductionDemand", "can_inactivate": kind in catalog,
        "archived": m.ArchivedRecord.objects.filter(company=request.company, source_model=model._meta.label, source_id=pk, archived=True).exists(),
        "record_url": op.record_url(model._meta.label, pk) or "/parametros/",
        "events": timeline(m.ActivityEvent.objects.filter(company=request.company, source_model=model._meta.label, source_id=pk))})


@require_POST
@tenant
def quote_status(request, pk):
    require_role(request)
    with transaction.atomic():
        quote = get_object_or_404(m.Quote.objects.select_for_update(), pk=pk, company=request.company)
        status = request.POST.get("status")
        if quote.status in {"converted", "cancelled"} or status not in {"draft", "sent", "waiting", "approved", "expired"}:
            messages.error(request, "Não é possível alterar este status.")
        else:
            quote.status = status
            quote.save(update_fields=["status", "updated_at"])
            messages.success(request, "Resposta/status registrado. Esta ação não envia mensagens externas.")
    return redirect(f"/orcamentos/?selected={pk}")


@tenant
def records_list(request):
    choices = {c.__name__: c for c in [m.Customer, m.Product, m.Supply, m.Filament, m.ConsignedStore, m.Printer, m.PaymentMethod, m.FinancialAccount, m.MaterialFamily, m.ProductCategory, m.ProductType, m.Order, m.Quote, m.QuoteRequest, m.Sale, m.Purchase, m.ConsignmentShipment]}
    kind = request.GET.get("kind", "Customer")
    model = choices.get(kind, m.Customer)
    rows = op.visible_records(model, request.company, request.GET.get("archived") == "1")
    if request.GET.get("status", "active") != "all": rows = rows.filter(active=request.GET.get("status", "active") == "active")
    q = request.GET.get("q", "").strip()
    if q:
        fields = {f.name for f in model._meta.fields}
        condition = Q()
        for field in ["name", "code", "sku", "color", "supplier"]:
            if field in fields: condition |= Q(**{f"{field}__icontains": q})
        rows = rows.filter(condition)
    records = [{"obj": o, "label": getattr(o, "name", None) or getattr(o, "code", None) or str(o), "url": op.record_url(model._meta.label, o.pk)} for o in rows[:200]]
    return render(request, "erp/records_list.html", {"title": "Cadastros e arquivo", "kinds": choices, "kind": model.__name__, "records": records})
