from datetime import date, datetime
from decimal import Decimal
from functools import wraps
from itertools import groupby
import hashlib
import io
import json
import uuid
from xml.sax.saxutils import escape

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.http import FileResponse, HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import (
    Alert,
    CalculationModel,
    Company,
    Composition,
    CompositionItem,
    CompositionSupply,
    ConsignedStore,
    ConsignmentBalance,
    ConsignmentSettlement,
    ConsignmentShipment,
    ConsignmentShipmentItem,
    Customer,
    Filament,
    FinancialAccount,
    FinancialEntry,
    IssuedDocument,
    ManufacturingPart,
    MaterialFamily,
    MaterialMovement,
    Order,
    PaymentMethod,
    Printer,
    Product,
    ProductCategory,
    ProductType,
    ProductionDemand,
    ProductionFailure,
    Purchase,
    PurchaseItem,
    Quote,
    QuoteRequest,
    Sale,
    Sequence,
    StockMovement,
    Supply,
    money,
)
from .services import (
    advance_demand,
    begin_once,
    finish_once,
    previous_result,
    close_roll,
    complete_order_calculation,
    complete_purchase,
    complete_settlement,
    complete_shipment,
    correct_purchase,
    create_manual_entries,
    create_replenishment,
    create_sale,
    create_settlement,
    move_product_stock,
    open_roll,
    quote_to_order,
    record_order_payment,
    request_to_direct_order,
    request_to_quote,
    register_production_failure,
    settle_financial_entry,
    to_decimal,
)


def company_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not getattr(request, "company", None):
            messages.error(request, "Seu usuário ainda não está vinculado a uma empresa.")
            return redirect("logout")
        if request.method == "POST" and getattr(getattr(request, "membership", None), "role", "viewer") == "viewer" and not request.user.is_superuser:
            raise PermissionDenied("Seu perfil é somente leitura.")
        return view(request, *args, **kwargs)
    return wrapped


def parse_date(value, default=None):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return default or timezone.localdate()


def optional_company_record(model, raw_id, company, **filters):
    """Empty form IDs mean create/unset; supplied IDs must identify a valid record."""
    value = str(raw_id).strip() if raw_id is not None else ""
    if not value:
        return None
    if not value.isascii() or not value.isdecimal() or len(value) > 19:
        raise ValidationError("Identificador inválido. Feche o formulário e tente novamente.")
    identifier = int(value)
    if not 0 < identifier <= 9223372036854775807:
        raise ValidationError("Identificador inválido. Feche o formulário e tente novamente.")
    record = model.objects.filter(pk=identifier, company=company, **filters).first()
    if record is None:
        raise ValidationError("Cadastro não encontrado ou indisponível para esta empresa. Atualize a página e selecione novamente.")
    return record


def safe_action(request, callback, success, redirect_to, *args, **kwargs):
    try:
        result = callback(*args, **kwargs)
        messages.success(request, success)
        return redirect(redirect_to), result
    except (ValidationError, ValueError) as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        messages.error(request, message)
        return redirect(redirect_to), None


def common_context(company):
    return {
        "customers": Customer.objects.filter(company=company, active=True),
        "payment_methods": PaymentMethod.objects.filter(company=company, active=True),
        "accounts": FinancialAccount.objects.filter(company=company, active=True),
        "printers": Printer.objects.filter(company=company, active=True),
        "families": MaterialFamily.objects.filter(company=company, active=True),
        "supplies": Supply.objects.filter(company=company, active=True),
        "products": Product.objects.filter(company=company, active=True),
        "product_categories": ProductCategory.objects.filter(company=company, active=True),
        "product_types": ProductType.objects.filter(company=company, active=True),
    }


def is_company_admin(request):
    return bool(request.user.is_superuser or request.user.erp_memberships.filter(company=request.company, active=True, role="admin").exists())


@company_required
def dashboard(request):
    from .operations import dashboard_operations, operating_demands, sort_demands
    from .reminders import refresh_due, visible_reminders
    company = request.company
    refresh_due(company)
    demands = operating_demands(company)
    products_low = Product.objects.filter(company=company, active=True, current_stock__lte=F("minimum_stock"))
    filaments_low = Filament.objects.filter(company=company, active=True).filter(Q(minimum_rolls__isnull=False, closed_rolls__lte=F("minimum_rolls")) | Q(minimum_rolls__isnull=True, closed_rolls__lte=company.default_filament_minimum))
    context = {
        "title": "Início",
        "today": timezone.localdate(),
        "cards": [
            ("Solicitações pendentes", QuoteRequest.objects.filter(company=company, active=True, status__in=["new", "analysis", "waiting"]).count(), "requests", "blue"),
            ("Orçamentos aguardando", Quote.objects.filter(company=company, active=True, status__in=["sent", "waiting"]).count(), "quotes", "orange"),
            ("Em fazer arte", demands.filter(stage="art").count(), "orders", "violet"),
            ("Aguardando material", demands.filter(stage="material").count(), "production", "orange"),
            ("Aguardando impressão", demands.filter(stage="queue").count(), "production", "blue"),
            ("Imprimindo", demands.filter(stage="printing").count(), "production", "green"),
            ("Entregas pendentes", demands.filter(stage="ready", order__delivered_at__isnull=True).count(), "orders", "orange"),
            ("Estoques abaixo do mínimo", products_low.count() + filaments_low.count(), "stock", "red"),
            ("Cálculos pendentes", Order.objects.filter(company=company, active=True, calculation_status="pending").count(), "orders", "violet"),
        ],
        "demands": sort_demands(list(demands.exclude(stage="ready")))[:10],
        "ready": demands.filter(stage="ready", order__delivered_at__isnull=True)[:7],
        "pending_requests": QuoteRequest.objects.filter(company=company, active=True, status__in=["new", "analysis", "waiting"]).select_related("customer")[:5],
        "waiting_quotes": Quote.objects.filter(company=company, active=True, status__in=["sent", "waiting", "draft"]).select_related("customer")[:5],
        "alerts": Alert.objects.filter(company=company, active=True, resolved_at__isnull=True)[:6],
        "products_low": products_low[:4],
        "filaments_low": filaments_low[:4],
    }
    context.update(dashboard_operations(company, list(visible_reminders(request).filter(status="due"))))
    context["cards"][6] = ("Entregas pendentes", len(context["ready_orders"]), "orders", "orange")
    return render(request, "erp/dashboard.html", context)


@company_required
@transaction.atomic
def requests_page(request):
    company = request.company
    if request.method == "POST":
        try:
            if not request.POST.get("description", "").strip():
                raise ValidationError("Informe a descrição da peça.")
            operation, created = begin_once(company, request.POST.get("idempotency_key"), "create_request")
            if not created:
                return redirect("requests")
            customer = get_object_or_404(Customer, pk=request.POST.get("customer"), company=company, active=True)
            reminder = None
            if request.POST.get("reminder_enabled"):
                value = f"{request.POST.get('reminder_date')} {request.POST.get('reminder_time')}"
                reminder = timezone.make_aware(datetime.strptime(value, "%Y-%m-%d %H:%M"))
            item = QuoteRequest.objects.create(
                company=company,
                code=Sequence.next(company, "SOL"),
                customer=customer,
                description=request.POST.get("description", "").strip(),
                notes=request.POST.get("notes", "").strip(),
                origin=request.POST.get("origin", "other"),
                reminder_at=reminder,
            )
            if reminder:
                purpose = request.POST.get("reminder_purpose", "manual")
                if purpose not in {"manual", "quote", "order"}:
                    raise ValidationError("Finalidade do lembrete inválida.")
                item.reminder.purpose = purpose
                item.reminder.save(update_fields=["purpose"])
            finish_once(operation, item)
            messages.success(request, f"Solicitação {item.code} criada.")
            action = request.POST.get("after_save")
            if action == "quote":
                quote = request_to_quote(item, request.POST.get("idempotency_key"))
                return redirect(f"/orcamentos/?selected={quote.pk}")
            if action == "order":
                order = request_to_direct_order(item, request.POST.get("idempotency_key"))
                return redirect(f"/pedidos/?selected={order.pk}")
            return redirect("requests")
        except (ValidationError, ValueError) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
            transaction.set_rollback(True)
            return redirect("requests")
    from .operations import visible_records
    context = common_context(company)
    context.update({
        "title": "Solicitações",
        "requests_list": visible_records(QuoteRequest, company).filter(active=True).select_related("customer", "reminder"),
        "idempotency_key": uuid.uuid4(),
        "today": timezone.localdate(),
    })
    if request.GET.get("reminders") == "due":
        from .reminders import refresh_due
        refresh_due(company)
        context["requests_list"] = context["requests_list"].filter(reminder__status="due")
    if request.GET.get("q"):
        context["requests_list"] = context["requests_list"].filter(Q(code__icontains=request.GET["q"]) | Q(customer__name__icontains=request.GET["q"]))
    context["selected_customer"] = request.GET.get("customer", "")
    return render(request, "erp/requests.html", context)


@require_POST
@company_required
def customer_quick_create(request):
    try:
        name = request.POST.get("name", "").strip()
        if not name:
            raise ValidationError("Informe o nome do cliente.")
        customer = Customer.objects.create(
            company=request.company,
            name=name,
            legal_name=request.POST.get("legal_name", "").strip(),
            document=request.POST.get("document", "").strip(),
            phone=request.POST.get("phone", "").strip(),
            whatsapp=request.POST.get("whatsapp", "").strip(),
            email=request.POST.get("email", "").strip(),
            city=request.POST.get("city", "").strip(),
            state=request.POST.get("state", "").strip().upper(),
        )
        return JsonResponse({"ok": True, "id": customer.pk, "name": customer.name})
    except Exception as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        return JsonResponse({"ok": False, "error": message}, status=400)


@require_POST
@company_required
def request_quote(request, pk):
    item = get_object_or_404(QuoteRequest, pk=pk, company=request.company, active=True)
    try:
        quote = request_to_quote(item, request.POST.get("idempotency_key"))
        messages.success(request, f"Orçamento {quote.code} criado.")
        return redirect(f"/orcamentos/?selected={quote.pk}")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("requests")


@require_POST
@company_required
def request_direct_order(request, pk):
    item = get_object_or_404(QuoteRequest, pk=pk, company=request.company, active=True)
    try:
        order = request_to_direct_order(item, request.POST.get("idempotency_key"), parse_date(request.POST.get("deadline"), None) if request.POST.get("deadline") else None)
        messages.success(request, f"Pedido direto {order.code} criado com cálculo pendente.")
        return redirect(f"/pedidos/?selected={order.pk}")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("requests")


@company_required
def quotes_page(request):
    company = request.company
    from .operations import visible_records
    quotes = visible_records(Quote, company, request.GET.get("archived") == "1").filter(active=True).select_related("customer", "request", "composition")
    if request.GET.get("q"): quotes = quotes.filter(Q(code__icontains=request.GET["q"]) | Q(customer__name__icontains=request.GET["q"]))
    if request.GET.get("status") == "waiting": quotes = quotes.filter(status__in=["sent", "waiting"])
    elif request.GET.get("status"): quotes = quotes.filter(status=request.GET["status"])
    selected = get_object_or_404(Quote, pk=request.GET["selected"], company=company) if request.GET.get("selected", "").isdigit() else quotes.first()
    context = common_context(company)
    context.update({"title": "Orçamentos", "quotes_list": quotes, "selected": selected, "idempotency_key": uuid.uuid4(), "quote_statuses": Quote.STATUSES})
    return render(request, "erp/quotes.html", context)


@require_POST
@company_required
def quote_update(request, pk):
    quote = get_object_or_404(Quote, pk=pk, company=request.company, active=True)
    quote.manual_value = to_decimal(request.POST.get("manual_value")) if request.POST.get("manual_value") else None
    quote.valid_until = parse_date(request.POST.get("valid_until"), quote.valid_until) if request.POST.get("valid_until") else quote.valid_until
    quote.payment_terms = request.POST.get("payment_terms", "").strip()
    quote.freight_amount = to_decimal(request.POST.get("freight_amount"))
    quote.notes = request.POST.get("notes", "").strip()
    if quote.manual_value is not None and (not quote.manual_value.is_finite() or quote.manual_value < 0):
        return JsonResponse({"error": "Informe um valor final válido, maior ou igual a zero."}, status=400)
    if quote.freight_amount < 0:
        return JsonResponse({"error": "O frete não pode ser negativo."}, status=400)
    if quote.manual_value is not None and quote.freight_amount > quote.manual_value:
        return JsonResponse({"error": "O frete não pode exceder o valor total do orçamento."}, status=400)
    quote.save(update_fields=["manual_value", "valid_until", "payment_terms", "freight_amount", "notes", "updated_at"])
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "profit": str(quote.expected_profit) if quote.expected_profit is not None else None, "margin": str(quote.effective_margin) if quote.effective_margin is not None else None})
    if quote.manual_value is not None and quote.expected_profit < 0:
        messages.warning(request, f"Valor salvo com prejuízo previsto de {pdf_brl(abs(quote.expected_profit))}.")
    else:
        messages.success(request, "Valor final e condições do orçamento salvos.")
    return redirect(f"/orcamentos/?selected={quote.pk}")


@require_POST
@company_required
def convert_quote(request, pk):
    quote = get_object_or_404(Quote, pk=pk, company=request.company, active=True)
    try:
        order = quote_to_order(quote, request.POST.get("idempotency_key"), parse_date(request.POST.get("deadline"), None) if request.POST.get("deadline") else None)
        messages.success(request, f"Orçamento convertido no pedido {order.code}.")
        return redirect(f"/pedidos/?selected={order.pk}")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect(f"/orcamentos/?selected={pk}")


@company_required
def orders_page(request):
    from .operation_views import orders_view
    return orders_view(request)


@require_POST
@company_required
def order_payment(request, pk):
    order = get_object_or_404(Order, pk=pk, company=request.company, active=True)
    method = get_object_or_404(PaymentMethod, pk=request.POST.get("payment_method"), company=request.company, active=True)
    account = get_object_or_404(FinancialAccount, pk=request.POST.get("account"), company=request.company, active=True)
    try:
        record_order_payment(order, method, account, request.POST.get("amount") or None, request.POST.get("idempotency_key"), request.POST.get("received_now") == "on", request.POST.get("notes", ""))
        messages.success(request, "Recebimento registrado.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect(f"/pedidos/?selected={pk}")


@require_POST
@company_required
@transaction.atomic
def deliver_order(request, pk):
    order = get_object_or_404(Order.objects.select_for_update(), pk=pk, company=request.company, active=True)
    if order.cancelled_at:
        messages.error(request, "Pedido cancelado não pode ser entregue.")
    elif not order.demands.filter(active=True).exists() or order.demands.filter(active=True).exclude(stage="ready").exists():
        messages.error(request, "O pedido precisa estar pronto antes da entrega.")
    elif order.delivered_at:
        messages.info(request, "Entrega já registrada.")
    else:
        order.delivered_at = timezone.now()
        order.save(update_fields=["delivered_at", "updated_at"])
        messages.success(request, "Entrega registrada.")
    return redirect(f"/pedidos/?selected={pk}")


@company_required
def production_page(request):
    from .operation_views import production_view
    return production_view(request)


@require_POST
@company_required
def production_advance(request, pk):
    demand = get_object_or_404(ProductionDemand, pk=pk, company=request.company, active=True)
    try:
        changed = advance_demand(demand, request.POST.get("stage"), request.user)
        messages.success(request, "Etapa atualizada.")
        if changed.order_id and request.POST.get("stage") == "ready":
            from .operations import order_state
            if order_state(changed.order)["all_ready"]:
                return redirect(f"/producao/?order={changed.order_id}&selected={pk}&completed={changed.order_id}")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect(f"/producao/?selected={pk}")


@require_POST
@company_required
def production_failure(request, pk):
    demand = get_object_or_404(ProductionDemand, pk=pk, company=request.company, active=True)
    part = ManufacturingPart.objects.filter(pk=request.POST.get("part"), company=request.company, active=True).first()
    try:
        failure = register_production_failure(
            demand,
            part,
            request.POST.get("failure_percent"),
            request.POST.get("reason", "Falha de impressão"),
            request.POST.get("notes", ""),
            key=request.POST.get("idempotency_key"),
        )
        messages.warning(request, f"Falha registrada. Custo adicional apurado pelo snapshot: {pdf_brl(failure.additional_cost)}.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect(f"/producao/?selected={pk}")


@company_required
@transaction.atomic
def catalog_page(request):
    company = request.company
    if request.method == "POST":
        try:
            category = optional_company_record(ProductCategory, request.POST.get("category"), company, active=True)
            product_type = optional_company_record(ProductType, request.POST.get("product_type"), company, active=True)
            product = optional_company_record(Product, request.POST.get("product_id"), company, active=True)
            if product is not None:
                sku = request.POST.get("sku", "").strip() or product.sku
                if Product.objects.filter(company=company, sku=sku).exclude(pk=product.pk).exists():
                    raise ValidationError("Este SKU já está em uso.")
                product.name = request.POST.get("name", "").strip()
                product.sku = sku
                product.category_ref = category
                product.product_type = product_type
                product.category = category.name if category else request.POST.get("category_text", "")
                product.description = request.POST.get("description", "")
                product.minimum_stock = to_decimal(request.POST.get("minimum_stock"))
                product.target_stock = to_decimal(request.POST.get("target_stock"))
                product.current_cost = to_decimal(request.POST.get("current_cost"), product.current_cost)
                product.current_price = to_decimal(request.POST.get("current_price"), product.current_price)
                if request.FILES.get("image"):
                    product.image = request.FILES["image"]
                product.save()
                messages.success(request, f"Produto {product.name} atualizado.")
                return redirect("catalog")
            model = CalculationModel.objects.filter(company=company, active=True, default=True).first()
            if not model:
                raise ValidationError("Cadastre um modelo de cálculo padrão.")
            name = request.POST.get("name", "").strip()
            composition = Composition.objects.create(company=company, name=name, calculation_model=model)
            CompositionItem.objects.create(company=company, composition=composition, name=name, quantity=1, unit="un")
            sku = request.POST.get("sku", "").strip() or Sequence.next_numeric(company)
            if Product.objects.filter(company=company, sku=sku).exists():
                raise ValidationError("Este SKU já está em uso.")
            product = Product.objects.create(
                company=company, name=name, sku=sku,
                category=category.name if category else request.POST.get("category_text", ""),
                category_ref=category, product_type=product_type,
                description=request.POST.get("description", ""), image=request.FILES.get("image"), composition=composition,
                minimum_stock=to_decimal(request.POST.get("minimum_stock")), target_stock=to_decimal(request.POST.get("target_stock")), current_stock=0,
                current_cost=to_decimal(request.POST.get("current_cost")), current_price=to_decimal(request.POST.get("current_price")),
            )
            messages.success(request, f"Produto {product.name} criado com SKU {product.sku}. Complete a composição para precificá-lo.")
            return redirect("composition", pk=composition.pk)
        except (ValidationError, ValueError) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
            transaction.set_rollback(True)
            return redirect("catalog")
    from .operations import visible_records
    products = visible_records(Product, company).filter(active=True).select_related("composition", "category_ref", "product_type").order_by("category_ref__name", "name")
    if request.GET.get("category"):
        products = products.filter(category_ref_id=request.GET.get("category"))
    if request.GET.get("type"):
        products = products.filter(product_type_id=request.GET.get("type"))
    if request.GET.get("q"):
        products = products.filter(Q(name__icontains=request.GET["q"]) | Q(sku__icontains=request.GET["q"]) | Q(description__icontains=request.GET["q"]))
    context = common_context(company)
    sequence = Sequence.objects.filter(company=company, prefix="SKU").first()
    next_value = (sequence.value if sequence else 0) + 1
    while Product.objects.filter(company=company, sku=str(next_value).zfill(4)).exists():
        next_value += 1
    groups = [{"grouper":category, "list":list(items)} for category,items in groupby(products, key=lambda product: product.display_category)] if request.GET.get("group", "1") == "1" else [{"grouper":"Todos os produtos", "list":products.order_by("name")}]
    context.update({"title": "Catálogo", "catalog_products": products, "catalog_groups":groups, "suggested_sku":str(next_value).zfill(4)})
    return render(request, "erp/catalog.html", context)


@company_required
@transaction.atomic
def stock_page(request):
    company = request.company
    if request.method == "POST":
        product = get_object_or_404(Product.objects.select_for_update(), pk=request.POST.get("product"), company=company, active=True)
        try:
            if request.POST.get("new_quantity") not in (None, ""):
                new_quantity = to_decimal(request.POST.get("new_quantity"))
                difference = new_quantity - product.current_stock
                if not request.POST.get("reason", "").strip():
                    raise ValidationError("Informe o motivo do ajuste por contagem.")
                move_product_stock(product, difference, "adjustment", request.user, None, f"Contagem física: {request.POST.get('reason')}")
                messages.success(request, f"Contagem ajustada. Diferença registrada: {difference}.")
            else:
                quantity = to_decimal(request.POST.get("quantity"))
                if request.POST.get("direction") == "out":
                    quantity = -abs(quantity)
                move_product_stock(product, quantity, request.POST.get("movement_type", "adjustment"), request.user, None, request.POST.get("note", "Ajuste manual"))
                messages.success(request, "Movimentação registrada com histórico.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        return redirect("stock")
    products = Product.objects.filter(company=company, active=True)
    from .operations import stock_distribution
    if request.GET.get("product", "").isdigit(): products = products.filter(pk=request.GET["product"])
    if request.GET.get("q"): products = products.filter(Q(sku__icontains=request.GET["q"]) | Q(name__icontains=request.GET["q"]))
    stock_products = list(products)
    for product in stock_products:
        product.distribution = stock_distribution(product)
        product.consigned_stock = sum((row["balance"].quantity for row in product.distribution), Decimal("0"))
        product.total_stock = product.current_stock + product.consigned_stock
    context = common_context(company)
    context.update({
        "title": "Estoque",
        "stock_products": stock_products,
        "movements": StockMovement.objects.filter(company=company, active=True).select_related("product", "user")[:30],
        "low_products": products.filter(current_stock__lte=F("minimum_stock")),
        "replenishments": ProductionDemand.objects.filter(company=company, active=True, origin="replenishment").exclude(stage="ready")[:10],
    })
    return render(request, "erp/stock.html", context)


@require_POST
@company_required
def replenish_product(request, pk):
    product = get_object_or_404(Product, pk=pk, company=request.company, active=True)
    try:
        demand = create_replenishment(product, request.POST.get("quantity") or None, request.user)
        messages.success(request, f"Reposição {demand.code} adicionada à fila de produção.")
        return redirect(f"/producao/?selected={demand.pk}")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("stock")


@company_required
@transaction.atomic
def materials_page(request):
    company = request.company
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "filament":
                family = get_object_or_404(MaterialFamily, pk=request.POST.get("family"), company=company, active=True)
                filament = optional_company_record(Filament, request.POST.get("filament_id"), company, active=True) or Filament(company=company)
                filament.family = family
                filament.color = request.POST.get("color")
                filament.color_hex = request.POST.get("color_hex", "")
                filament.brand = request.POST.get("brand", "")
                filament.diameter_mm = to_decimal(request.POST.get("diameter"), "1.75")
                filament.nominal_weight_g = int(request.POST.get("weight") or 1000)
                filament.supplier = request.POST.get("supplier", "")
                filament.unit_cost = to_decimal(request.POST.get("cost"))
                filament.minimum_rolls = int(request.POST.get("minimum")) if request.POST.get("minimum") else None
                filament.save()
                messages.success(request, "Filamento atualizado." if request.POST.get("filament_id") else "Filamento cadastrado. O estoque continua zerado até uma compra.")
            elif action == "supply":
                supply = optional_company_record(Supply, request.POST.get("supply_id"), company, active=True) or Supply(company=company, physical_stock=0, reserved_stock=0)
                supply.name = request.POST.get("name")
                supply.unit = request.POST.get("unit", "un")
                supply.supplier = request.POST.get("supplier", "")
                supply.unit_cost = to_decimal(request.POST.get("cost"))
                supply.minimum_stock = to_decimal(request.POST.get("minimum"))
                supply.save()
                messages.success(request, "Insumo atualizado." if request.POST.get("supply_id") else "Insumo cadastrado. O estoque continua zerado até uma compra.")
            elif action == "filament_adjust":
                filament = get_object_or_404(Filament.objects.select_for_update(), pk=request.POST.get("filament_id"), company=company, active=True)
                reason = request.POST.get("reason", "").strip()
                if not reason:
                    raise ValidationError("Informe o motivo do ajuste.")
                before = {"closed": filament.closed_rolls, "open": filament.open_rolls}
                new_closed = int(request.POST.get("closed_rolls") or 0)
                new_open = int(request.POST.get("open_rolls") or 0)
                if new_closed < 0 or new_open < 0:
                    raise ValidationError("As quantidades de rolos não podem ser negativas.")
                filament.closed_rolls = new_closed
                filament.open_rolls = new_open
                filament.save(update_fields=["closed_rolls", "open_rolls", "updated_at"])
                MaterialMovement.objects.create(
                    company=company, movement_type="adjustment", filament=filament,
                    quantity=Decimal(new_closed - before["closed"]), note=reason, user=request.user,
                    details={"before": before, "after": {"closed": new_closed, "open": new_open}},
                )
                messages.success(request, "Estoque de filamento ajustado com histórico.")
            elif action == "supply_adjust":
                supply = get_object_or_404(Supply.objects.select_for_update(), pk=request.POST.get("supply_id"), company=company, active=True)
                reason = request.POST.get("reason", "").strip()
                if not reason:
                    raise ValidationError("Informe o motivo do ajuste.")
                new_stock = to_decimal(request.POST.get("physical_stock"))
                if new_stock < 0:
                    raise ValidationError("O estoque físico contado não pode ser negativo.")
                if new_stock < supply.reserved_stock:
                    if not request.POST.get("admin_confirmation") or not is_company_admin(request):
                        raise ValidationError("O novo estoque é menor que o reservado. A confirmação de um administrador é obrigatória.")
                    Alert.objects.create(
                        company=company, level="critical", title=f"Reserva superior ao estoque: {supply.name}",
                        message=f"Estoque físico ajustado para {new_stock} {supply.unit}, mantendo reserva de {supply.reserved_stock} {supply.unit}.",
                        source_type="erp.Supply", source_id=str(supply.pk),
                    )
                before = supply.physical_stock
                supply.physical_stock = new_stock
                supply.save(update_fields=["physical_stock", "updated_at"])
                MaterialMovement.objects.create(
                    company=company, movement_type="adjustment", supply=supply, quantity=new_stock - before,
                    note=reason, user=request.user, details={"before": str(before), "after": str(new_stock), "reserved": str(supply.reserved_stock)},
                )
                messages.success(request, "Estoque de insumo ajustado; a reserva foi preservada.")
            elif action == "purchase":
                operation, created = begin_once(company, request.POST.get("idempotency_key"), "create_purchase")
                if not created:
                    return redirect("materials")
                method = get_object_or_404(PaymentMethod, pk=request.POST.get("payment_method"), company=company, active=True)
                account = get_object_or_404(FinancialAccount, pk=request.POST.get("account"), company=company, active=True)
                quantity = to_decimal(request.POST.get("quantity"))
                unit_cost = to_decimal(request.POST.get("unit_cost"))
                material_class = Filament if request.POST.get("material_type") == "filament" else Supply
                material = get_object_or_404(material_class, pk=request.POST.get("material_id"), company=company, active=True)
                if quantity <= 0 or unit_cost < 0 or (material_class is Filament and quantity != quantity.to_integral_value()):
                    raise ValidationError("Informe quantidade positiva (rolos inteiros) e custo não negativo.")
                purchase = Purchase.objects.create(
                    company=company, code=Sequence.next(company, "COM"), supplier=request.POST.get("supplier"),
                    purchase_date=parse_date(request.POST.get("purchase_date")), payment_method=method, account=account,
                    installments=int(request.POST.get("installments") or 1), first_due_date=parse_date(request.POST.get("first_due_date")),
                    total=money(quantity * unit_cost), notes=request.POST.get("notes", ""),
                )
                kwargs = {"filament_id": request.POST.get("material_id")} if request.POST.get("material_type") == "filament" else {"supply_id": request.POST.get("material_id")}
                PurchaseItem.objects.create(company=company, purchase=purchase, quantity=quantity, unit_cost=unit_cost, total=money(quantity * unit_cost), **kwargs)
                finish_once(operation, purchase)
                if request.POST.get("confirm"):
                    complete_purchase(purchase, account, request.user)
                    messages.success(request, f"Compra {purchase.code} concluída: estoque e financeiro atualizados.")
                else:
                    messages.success(request, f"Compra {purchase.code} salva como rascunho.")
            elif action == "purchase_confirm":
                purchase = get_object_or_404(Purchase, pk=request.POST.get("purchase_id"), company=company, active=True)
                if not purchase.account:
                    raise ValidationError("Selecione uma conta antes de confirmar a compra.")
                complete_purchase(purchase, purchase.account, request.user)
                messages.success(request, f"Compra {purchase.code} confirmada.")
            elif action == "purchase_correct":
                purchase = get_object_or_404(Purchase, pk=request.POST.get("purchase_id"), company=company, active=True)
                if not purchase.completed_at and "supplier" in request.POST:
                    purchase.supplier = request.POST.get("supplier", "").strip()
                    purchase.purchase_date = parse_date(request.POST.get("purchase_date"), purchase.purchase_date)
                    purchase.payment_method = get_object_or_404(PaymentMethod, pk=request.POST.get("payment_method"), company=company, active=True)
                    purchase.account = get_object_or_404(FinancialAccount, pk=request.POST.get("account"), company=company, active=True)
                    purchase.installments = max(1, int(request.POST.get("installments") or 1))
                    purchase.first_due_date = parse_date(request.POST.get("first_due_date"), purchase.first_due_date)
                    purchase.save()
                if purchase.completed_at and not purchase.account:
                    purchase.account = get_object_or_404(FinancialAccount, pk=request.POST.get("account"), company=company, active=True)
                    purchase.save(update_fields=["account", "updated_at"])
                correct_purchase(purchase, request.POST.get("quantity"), request.POST.get("unit_cost"), request.POST.get("reason", ""), request.user)
                messages.success(request, f"Compra {purchase.code} corrigida com movimentos compensatórios; nenhum histórico foi apagado.")
            return redirect("materials")
        except (ValidationError, ValueError) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
            transaction.set_rollback(True)
            return redirect("materials")
    from .operations import visible_records
    filaments = visible_records(Filament, company).filter(active=True).select_related("family")
    supplies = visible_records(Supply, company).filter(active=True)
    if request.GET.get("q"):
        filaments = filaments.filter(Q(family__name__icontains=request.GET["q"]) | Q(color__icontains=request.GET["q"]))
        supplies = supplies.filter(name__icontains=request.GET["q"])
    context = common_context(company)
    context.update({
        "title": "Materiais",
        "filaments": filaments,
        "materials_supplies": supplies,
        "purchases": visible_records(Purchase, company).filter(active=True).select_related("payment_method", "account").prefetch_related("items__filament", "items__supply"),
        "material_movements": MaterialMovement.objects.filter(company=company, active=True).select_related("filament__family", "supply")[:30],
        "low_filaments": [item for item in filaments if item.stock_status != "ok"],
        "low_supplies": [item for item in supplies if item.stock_status != "ok"],
        "today": timezone.localdate(),
        "idempotency_key": uuid.uuid4(),
    })
    return render(request, "erp/materials.html", context)


@require_POST
@company_required
def material_open_roll(request, pk):
    filament = get_object_or_404(Filament, pk=pk, company=request.company, active=True)
    try:
        open_roll(filament, request.user)
        messages.success(request, f"Rolo de {filament} aberto.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("materials")


@require_POST
@company_required
def material_close_roll(request, pk):
    filament = get_object_or_404(Filament, pk=pk, company=request.company, active=True)
    try:
        close_roll(filament, request.user)
        messages.success(request, f"Rolo de {filament} finalizado sem pesagem.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("materials")


@company_required
def customers_page(request):
    company = request.company
    if request.method == "POST":
        try:
            customer = optional_company_record(Customer, request.POST.get("customer_id"), company, active=True) or Customer(company=company)
            customer.name = request.POST.get("name")
            customer.legal_name = request.POST.get("legal_name", "")
            customer.document = request.POST.get("document", "")
            customer.phone = request.POST.get("phone", "")
            customer.whatsapp = request.POST.get("whatsapp", "")
            customer.instagram = request.POST.get("instagram", "")
            customer.email = request.POST.get("email", "")
            customer.city = request.POST.get("city", "")
            customer.state = request.POST.get("state", "").upper()
            customer.notes = request.POST.get("notes", "")
            customer.save()
            messages.success(request, f"Cliente {customer.name} {'atualizado' if request.POST.get('customer_id') else 'cadastrado'}.")
            return redirect("customer_detail", pk=customer.pk)
        except Exception as exc:
            messages.error(request, f"Não foi possível cadastrar: {exc}")
    from .operations import visible_records
    customers = visible_records(Customer, company, request.GET.get("archived") == "1").annotate(order_count=Count("orders"))
    if request.GET.get("status", "active") != "all": customers = customers.filter(active=request.GET.get("status", "active") == "active")
    if request.GET.get("q"): customers = customers.filter(Q(name__icontains=request.GET["q"]) | Q(phone__icontains=request.GET["q"]))
    if request.GET.get("open_orders"): customers = customers.filter(orders__delivered_at__isnull=True, orders__cancelled_at__isnull=True, orders__active=True).distinct()
    if request.GET.get("balance"): customers = [c for c in customers if c.balance_due > 0]
    context = {"title": "Clientes", "customers_list": customers}
    return render(request, "erp/customers.html", context)


@company_required
def customer_detail(request, pk):
    from .operation_views import customer_360
    return customer_360(request, pk)


@company_required
def pos_page(request):
    company = request.company
    if request.method == "POST":
        try:
            customer = get_object_or_404(Customer, pk=request.POST.get("customer"), company=company, active=True)
            method = get_object_or_404(PaymentMethod, pk=request.POST.get("payment_method"), company=company, active=True)
            account = get_object_or_404(FinancialAccount, pk=request.POST.get("account"), company=company, active=True)
            raw_cart = json.loads(request.POST.get("cart", "[]"))
            cart = []
            for row in raw_cart:
                product = get_object_or_404(Product, pk=row.get("product"), company=company, active=True)
                cart.append((product, row.get("quantity")))
            sale = create_sale(company, customer, method, cart, account, request.POST.get("idempotency_key"), request.user)
            messages.success(request, f"Venda {sale.code} finalizada. Estoque e financeiro atualizados.")
            return redirect("pos")
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
    context = common_context(company)
    context.update({"title": "Registrar venda", "sales": Sale.objects.filter(company=company, active=True).select_related("customer", "payment_method")[:15], "idempotency_key": uuid.uuid4()})
    return render(request, "erp/pos.html", context)


@company_required
@transaction.atomic
def consignment_page(request):
    company = request.company
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "store":
                store = optional_company_record(ConsignedStore, request.POST.get("store_id"), company) or ConsignedStore(company=company)
                store.name = request.POST.get("name", "").strip()
                store.contact_name = request.POST.get("contact_name", "")
                store.phone = request.POST.get("phone", "")
                store.address = request.POST.get("address", "")
                store.notes = request.POST.get("notes", "")
                store.default_commission_percent = to_decimal(request.POST.get("commission"))
                if not store.name or not 0 <= store.default_commission_percent <= 100:
                    raise ValidationError("Informe nome e comissão entre 0% e 100%.")
                store.save()
                messages.success(request, "Estabelecimento consignado salvo sem alterar as movimentações anteriores.")
            elif action == "shipment":
                store = get_object_or_404(ConsignedStore, pk=request.POST.get("store"), company=company, active=True)
                product = get_object_or_404(Product, pk=request.POST.get("product"), company=company, active=True)
                shipment = ConsignmentShipment.objects.create(company=company, code=Sequence.next(company, "REM"), store=store, shipment_date=parse_date(request.POST.get("date")))
                ConsignmentShipmentItem.objects.create(
                    company=company, shipment=shipment, product=product, quantity=to_decimal(request.POST.get("quantity")),
                    reference_price=to_decimal(request.POST.get("price")) or product.current_price,
                    commission_percent=to_decimal(request.POST.get("commission")) or store.default_commission_percent,
                    snapshot={"product": product.name, "price": str(product.current_price)},
                )
                messages.success(request, f"Remessa {shipment.code} criada. Confirme para movimentar o estoque.")
            elif action == "settlement":
                store = get_object_or_404(ConsignedStore, pk=request.POST.get("store"), company=company, active=True)
                found = json.loads(request.POST.get("found_quantities", "{}"))
                settlement = create_settlement(company, store, found, request.POST.get("period_reference"))
                if settlement.status == "blocked":
                    messages.error(request, "A prestação foi bloqueada por contagem maior que o esperado. Corrija a contagem ou registre o ajuste/remessa ausente.")
                else:
                    messages.success(request, f"Prestação {settlement.code} calculada. Revise e conclua.")
            return redirect("consignment")
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
            transaction.set_rollback(True)
            return redirect("consignment")
    context = common_context(company)
    context.update({
        "title": "Consignação",
        "stores": ConsignedStore.objects.filter(company=company, active=True),
        "balances": ConsignmentBalance.objects.filter(company=company, active=True).select_related("store", "product"),
        "shipments": ConsignmentShipment.objects.filter(company=company, active=True).select_related("store"),
        "settlements": ConsignmentSettlement.objects.filter(company=company, active=True).select_related("store").prefetch_related("items__product"),
        "today": timezone.localdate(),
    })
    if request.GET.get("store", "").isdigit():
        for key in ["balances", "shipments", "settlements"]: context[key] = context[key].filter(store_id=request.GET["store"])
    if request.GET.get("shipment", "").isdigit(): context["shipments"] = context["shipments"].filter(pk=request.GET["shipment"])
    if request.GET.get("q"):
        context["stores"] = context["stores"].filter(name__icontains=request.GET["q"])
        for key in ["balances", "shipments", "settlements"]: context[key] = context[key].filter(store__name__icontains=request.GET["q"])
    from .models import ArchivedRecord
    context["shipments"] = context["shipments"].exclude(pk__in=ArchivedRecord.objects.filter(company=company, source_model="erp.ConsignmentShipment", archived=True).values("source_id"))
    return render(request, "erp/consignment.html", context)


@company_required
@transaction.atomic
def shipment_new(request):
    company = request.company
    if request.method == "POST":
        try:
            operation, created = begin_once(company, request.POST.get("idempotency_key"), "create_shipment")
            if not created:
                return redirect("consignment")
            store = get_object_or_404(ConsignedStore, pk=request.POST.get("store"), company=company, active=True)
            shipment = ConsignmentShipment.objects.create(
                company=company, code=Sequence.next(company, "REM"), store=store,
                shipment_date=parse_date(request.POST.get("date")), notes=request.POST.get("notes", ""),
            )
            product_ids = request.POST.getlist("product")
            quantities = request.POST.getlist("quantity")
            prices = request.POST.getlist("price")
            commissions = request.POST.getlist("commission")
            created = 0
            for index, product_id in enumerate(product_ids):
                if not product_id or to_decimal(quantities[index] if index < len(quantities) else 0) <= 0:
                    continue
                product = get_object_or_404(Product, pk=product_id, company=company, active=True)
                quantity = to_decimal(quantities[index])
                price = to_decimal(prices[index]) if index < len(prices) and prices[index] != "" else product.current_price
                commission = to_decimal(commissions[index]) if index < len(commissions) and commissions[index] != "" else store.default_commission_percent
                if price < 0 or not 0 <= commission <= 100:
                    raise ValidationError("Preço não pode ser negativo e comissão deve estar entre 0% e 100%.")
                ConsignmentShipmentItem.objects.create(
                    company=company, shipment=shipment, product=product, quantity=quantity,
                    reference_price=price, commission_percent=commission,
                    snapshot={"product": product.name, "sku": product.sku, "price": str(product.current_price)},
                )
                created += 1
            if not created:
                raise ValidationError("Adicione ao menos um produto à remessa.")
            finish_once(operation, shipment)
            if request.POST.get("confirm"):
                complete_shipment(shipment, request.user)
                messages.success(request, f"Remessa {shipment.code} concluída e estoque transferido.")
            else:
                messages.success(request, f"Remessa {shipment.code} salva como rascunho.")
            return redirect("consignment")
        except (ValidationError, ValueError) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
            transaction.set_rollback(True)
            return redirect("shipment_new")
    context = common_context(company)
    context.update({
        "title": "Nova remessa consignada", "stores": ConsignedStore.objects.filter(company=company, active=True),
        "products": Product.objects.filter(company=company, active=True).order_by("name"),
        "today": timezone.localdate(), "selected_store": request.GET.get("store", ""),
        "idempotency_key": uuid.uuid4(),
    })
    return render(request, "erp/shipment_form.html", context)


@require_POST
@company_required
def shipment_complete(request, pk):
    shipment = get_object_or_404(ConsignmentShipment, pk=pk, company=request.company, active=True)
    try:
        complete_shipment(shipment, request.user)
        messages.success(request, "Remessa concluída e estoque transferido para consignação.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("consignment")


@require_POST
@company_required
@transaction.atomic
def settlement_complete(request, pk):
    settlement = get_object_or_404(ConsignmentSettlement.objects.select_for_update(), pk=pk, company=request.company, active=True)
    if settlement.status == "completed":
        return redirect("consignment")
    account = get_object_or_404(FinancialAccount, pk=request.POST.get("account"), company=request.company, active=True)
    method = get_object_or_404(PaymentMethod, pk=request.POST.get("payment_method"), company=request.company, active=True)
    try:
        for item in settlement.items.filter(active=True):
            disposition = request.POST.get(f"disposition_{item.pk}")
            if disposition in {"remain", "return"}:
                item.disposition = disposition
                item.save(update_fields=["disposition", "updated_at"])
        complete_settlement(settlement, account, method, request.user)
        messages.success(request, "Prestação concluída, saldo consignado e financeiro atualizados.")
        if request.POST.get("new_shipment"):
            return redirect(f"/consignacao/remessas/nova/?store={settlement.store_id}")
    except ValidationError as exc:
        transaction.set_rollback(True)
        messages.error(request, "; ".join(exc.messages))
    return redirect("consignment")


@company_required
def finance_page(request):
    company = request.company
    if request.method == "POST":
        try:
            direction = request.POST.get("direction")
            account = get_object_or_404(FinancialAccount, pk=request.POST.get("account"), company=company, active=True)
            method = optional_company_record(PaymentMethod, request.POST.get("payment_method"), company, active=True)
            customer = optional_company_record(Customer, request.POST.get("customer"), company, active=True)
            created = create_manual_entries(
                company, direction, request.POST.get("description"), request.POST.get("category"), request.POST.get("amount"),
                account, method, parse_date(request.POST.get("due_date")), request.POST.get("paid_now") == "on",
                request.POST.get("installments", 1), customer, request.POST.get("supplier", ""), request.POST.get("notes", ""),
            )
            messages.success(request, f"{len(created)} lançamento(s) registrado(s).")
            return redirect("finance")
        except (ValidationError, ValueError) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
    entries = FinancialEntry.objects.filter(company=company, active=True).select_related("account", "customer", "payment_method")
    accounts = FinancialAccount.objects.filter(company=company, active=True)
    received_month = entries.filter(direction="in", status="paid", issue_date__month=timezone.localdate().month, issue_date__year=timezone.localdate().year).aggregate(total=Sum("net_amount"))["total"] or 0
    paid_month = entries.filter(direction="out", status="paid", issue_date__month=timezone.localdate().month, issue_date__year=timezone.localdate().year).aggregate(total=Sum("net_amount"))["total"] or 0
    filtered = entries
    if request.GET.get("entry", "").isdigit(): filtered = filtered.filter(pk=request.GET["entry"])
    if request.GET.get("customer", "").isdigit(): filtered = filtered.filter(customer_id=request.GET["customer"])
    if request.GET.get("overdue"): filtered = filtered.filter(status="pending", due_date__lt=timezone.localdate())
    if request.GET.get("status"): filtered = filtered.filter(status=request.GET["status"])
    if request.GET.get("q"): filtered = filtered.filter(Q(code__icontains=request.GET["q"]) | Q(description__icontains=request.GET["q"]) | Q(customer__name__icontains=request.GET["q"]))
    from .operations import record_url
    shown = list(filtered[:200])
    for entry in shown: entry.origin_url = record_url(entry.source_type, entry.source_id) if str(entry.source_id).isdigit() else ""
    context = common_context(company)
    context.update({
        "title": "Financeiro", "entries": shown, "finance_accounts": accounts,
        "current_balance": sum((item.balance for item in accounts), Decimal("0")), "received_month": received_month,
        "paid_month": paid_month, "profit_month": received_month - paid_month,
        "receivable": entries.filter(direction="in", status="pending").aggregate(total=Sum("net_amount"))["total"] or 0,
        "payable": entries.filter(direction="out", status="pending").aggregate(total=Sum("net_amount"))["total"] or 0,
        "today": timezone.localdate(),
    })
    return render(request, "erp/finance.html", context)


@require_POST
@company_required
def finance_settle(request, pk):
    entry = get_object_or_404(FinancialEntry, pk=pk, company=request.company, active=True)
    try:
        settle_financial_entry(entry)
        messages.success(request, "Lançamento liquidado.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("finance")


@company_required
def reports_page(request):
    company = request.company
    start = parse_date(request.GET.get("start"), timezone.localdate().replace(day=1))
    end = parse_date(request.GET.get("end"), timezone.localdate())
    sales = Sale.objects.filter(company=company, active=True, created_at__date__range=(start, end))
    entries = FinancialEntry.objects.filter(company=company, active=True, issue_date__range=(start, end))
    orders = Order.objects.filter(company=company, active=True, created_at__date__range=(start, end))
    context = {
        "title": "Relatórios", "start": start, "end": end,
        "sales_total": sales.aggregate(total=Sum("gross_amount"))["total"] or 0,
        "profit_total": sales.aggregate(total=Sum("profit_amount"))["total"] or 0,
        "orders_count": orders.count(),
        "production_count": ProductionDemand.objects.filter(company=company, active=True, created_at__date__range=(start, end)).count(),
        "failure_count": ProductionFailure.objects.filter(company=company, active=True, created_at__date__range=(start, end)).count(),
        "receivables": entries.filter(direction="in", status="pending").aggregate(total=Sum("net_amount"))["total"] or 0,
        "payables": entries.filter(direction="out", status="pending").aggregate(total=Sum("net_amount"))["total"] or 0,
        "recent_sales": sales.select_related("customer")[:10], "recent_orders": orders.select_related("customer")[:10],
        "issued_documents": IssuedDocument.objects.filter(company=company).order_by("-created_at")[:30],
    }
    return render(request, "erp/reports.html", context)


@company_required
@transaction.atomic
def settings_page(request):
    company = request.company
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "company":
                for field in [
                    "name", "trading_name", "document", "state_registration", "responsible_name", "slogan",
                    "primary_color", "secondary_color", "success_color", "warning_color", "phone", "whatsapp",
                    "email", "instagram", "website", "address", "city", "state", "postal_code",
                    "material_cost_policy", "pricing_method",
                ]:
                    if field in request.POST:
                        setattr(company, field, request.POST.get(field))
                for field in ["energy_rate", "labor_hour_rate", "fixed_cost_per_order", "waste_percent", "default_margin_percent"]:
                    setattr(company, field, to_decimal(request.POST.get(field)))
                company.default_filament_minimum = int(request.POST.get("default_filament_minimum") or 2)
                if request.FILES.get("logo"):
                    company.logo = request.FILES["logo"]
                company.save()
                messages.success(request, "Parâmetros da empresa atualizados.")
            elif action == "printer":
                printer = optional_company_record(Printer, request.POST.get("printer_id"), company) or Printer(company=company)
                printer.name = request.POST.get("name")
                printer.model = request.POST.get("model", "")
                printer.acquisition_cost = to_decimal(request.POST.get("cost"))
                printer.useful_life_hours = int(request.POST.get("life") or 10000)
                printer.residual_percent = to_decimal(request.POST.get("residual"))
                printer.power_watts = int(request.POST.get("power") or 0)
                printer.maintenance_per_hour = to_decimal(request.POST.get("maintenance"))
                printer.save()
                messages.success(request, "Impressora salva.")
            elif action == "family":
                cost = to_decimal(request.POST.get("cost"))
                family = optional_company_record(MaterialFamily, request.POST.get("family_id"), company) or MaterialFamily(company=company, last_cost_kg=cost, weighted_cost_kg=cost)
                family.name = request.POST.get("name")
                family.reference_cost_kg = cost
                family.manual_cost_kg = cost
                family.save()
                messages.success(request, "Família de material salva.")
            elif action == "payment":
                payment = optional_company_record(PaymentMethod, request.POST.get("payment_id"), company) or PaymentMethod(company=company)
                payment.name = request.POST.get("name")
                payment.kind = request.POST.get("kind")
                payment.installments = int(request.POST.get("installments") or 1)
                payment.fee_percent = to_decimal(request.POST.get("fee"))
                payment.days_to_receive = int(request.POST.get("days") or 0)
                payment.save()
                messages.success(request, "Forma de pagamento salva.")
            elif action == "calculation":
                rules = {}
                for component in ["material", "labor", "energy", "maintenance", "depreciation", "supplies", "waste"]:
                    calculate = bool(request.POST.get(f"{component}_calculate"))
                    rules[component] = {
                        "calculate": calculate,
                        "cost": calculate and bool(request.POST.get(f"{component}_cost")),
                        "margin": calculate and bool(request.POST.get(f"{component}_margin")),
                    }
                calculation = optional_company_record(CalculationModel, request.POST.get("calculation_id"), company) or CalculationModel(company=company)
                if request.POST.get("default"):
                    CalculationModel.objects.filter(company=company).update(default=False)
                calculation.name = request.POST.get("name")
                calculation.description = request.POST.get("description", "")
                calculation.pricing_method = request.POST.get("pricing_method", "margin")
                calculation.margin_percent = to_decimal(request.POST.get("margin"))
                calculation.tax_percent = to_decimal(request.POST.get("tax"))
                calculation.component_rules = rules
                calculation.default = bool(request.POST.get("default"))
                calculation.active = bool(request.POST.get("active"))
                calculation.save()
                messages.success(request, "Modelo de cálculo salvo.")
            elif action in {"product_category", "product_type"}:
                model_class = ProductCategory if action == "product_category" else ProductType
                item = optional_company_record(model_class, request.POST.get("item_id"), company) or model_class(company=company)
                item.name = request.POST.get("name", "").strip()
                item.description = request.POST.get("description", "").strip()
                item.save()
                messages.success(request, "Cadastro salvo.")
            elif action == "toggle":
                model_class = {
                    "printer": Printer, "family": MaterialFamily, "payment": PaymentMethod,
                    "calculation": CalculationModel, "category": ProductCategory, "type": ProductType,
                }.get(request.POST.get("entity"))
                if not model_class:
                    raise ValidationError("Cadastro inválido.")
                item = get_object_or_404(model_class, pk=request.POST.get("item_id"), company=company)
                item.active = not item.active
                item.save(update_fields=["active", "updated_at"])
                messages.success(request, "Status atualizado.")
            return redirect("settings")
        except (ValidationError, ValueError) as exc:
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
            transaction.set_rollback(True)
            return redirect("settings")
    context = common_context(company)
    context.update({
        "title": "Parâmetros", "company": company,
        "calculation_models": CalculationModel.objects.filter(company=company),
        "all_printers": Printer.objects.filter(company=company),
        "all_families": MaterialFamily.objects.filter(company=company),
        "all_payments": PaymentMethod.objects.filter(company=company),
        "all_categories": ProductCategory.objects.filter(company=company),
        "all_product_types": ProductType.objects.filter(company=company),
    })
    return render(request, "erp/settings.html", context)


@company_required
@transaction.atomic
def composition_page(request, pk):
    composition = get_object_or_404(Composition, pk=pk, company=request.company, active=True)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if hasattr(composition, "order"):
                from .services import validate_order_composition_edit
                validate_order_composition_edit(composition.order)
            if action in {"item", "item_update", "part", "supply"}:
                quantity = to_decimal(request.POST.get("quantity"), "0")
                if not quantity.is_finite() or quantity <= 0:
                    raise ValidationError("A quantidade deve ser maior que zero.")
            if action in {"item", "item_update", "part"} and not request.POST.get("name", "").strip():
                raise ValidationError("Informe o nome.")
            if action == "part" and (to_decimal(request.POST.get("grams")) < 0 or int(request.POST.get("print_minutes") or 0) < 0):
                raise ValidationError("Peso e tempo não podem ser negativos.")
            if action == "meta" and (int(request.POST.get("labor_minutes") or 0) < 0 or not 0 <= to_decimal(request.POST.get("discount")) <= 100):
                raise ValidationError("Mão de obra deve ser positiva e desconto deve estar entre 0% e 100%.")
            if action == "meta":
                for field in ["margin_override", "waste_override"]:
                    if field in request.POST:
                        value = to_decimal(request.POST[field]) if request.POST[field].strip() else None
                        if value is not None and (value < 0 or (field == "waste_override" and value > 100)):
                            raise ValidationError("Margem não pode ser negativa; desperdício deve estar entre 0% e 100%.")
                        setattr(composition, field, value)
                composition.labor_minutes = int(request.POST.get("labor_minutes") or 0)
                composition.discount_percent = to_decimal(request.POST.get("discount"))
                composition.calculation_model = get_object_or_404(CalculationModel, pk=request.POST.get("calculation_model"), company=request.company, active=True)
                composition.calculated_at = None
                composition.save()
            elif action == "item":
                CompositionItem.objects.create(
                    company=request.company, composition=composition, name=request.POST.get("name"),
                    description=request.POST.get("description", ""), quantity=to_decimal(request.POST.get("quantity"), "1"), unit=request.POST.get("unit", "un"),
                )
                composition.calculated_at = None
                composition.save(update_fields=["calculated_at", "updated_at"])
            elif action == "item_update":
                item = get_object_or_404(CompositionItem, pk=request.POST.get("item"), company=request.company, composition=composition, active=True)
                item.name = request.POST.get("name")
                item.description = request.POST.get("description", "")
                item.quantity = to_decimal(request.POST.get("quantity"), "1")
                item.unit = request.POST.get("unit", "un")
                item.save()
                composition.calculated_at = None
                composition.save(update_fields=["calculated_at", "updated_at"])
            elif action == "item_duplicate":
                source = get_object_or_404(CompositionItem.objects.prefetch_related("parts", "supplies"), pk=request.POST.get("item"), company=request.company, composition=composition, active=True)
                duplicate = CompositionItem.objects.create(company=request.company, composition=composition, name=f"{source.name} (cópia)", description=source.description, quantity=source.quantity, unit=source.unit)
                for part in source.parts.filter(active=True):
                    ManufacturingPart.objects.create(company=request.company, item=duplicate, name=part.name, material_family=part.material_family, grams=part.grams, print_minutes=part.print_minutes, printer=part.printer, quantity=part.quantity)
                for use in source.supplies.filter(active=True):
                    CompositionSupply.objects.create(company=request.company, item=duplicate, supply=use.supply, quantity=use.quantity)
                composition.calculated_at = None
                composition.save(update_fields=["calculated_at", "updated_at"])
            elif action == "item_delete":
                item = get_object_or_404(CompositionItem, pk=request.POST.get("item"), company=request.company, composition=composition, active=True)
                if composition.items.filter(active=True).count() <= 1:
                    raise ValidationError("A composição deve manter ao menos um item.")
                item.active = False
                item.save(update_fields=["active", "updated_at"])
                composition.calculated_at = None
                composition.save(update_fields=["calculated_at", "updated_at"])
            elif action == "part":
                item = get_object_or_404(CompositionItem, pk=request.POST.get("item"), company=request.company, composition=composition, active=True)
                family = get_object_or_404(MaterialFamily, pk=request.POST.get("family"), company=request.company, active=True)
                printer = optional_company_record(Printer, request.POST.get("printer"), request.company, active=True)
                part = optional_company_record(ManufacturingPart, request.POST.get("part_id"), request.company, item__composition=composition, active=True) or ManufacturingPart(company=request.company)
                part.item = item
                part.name = request.POST.get("name")
                part.material_family = family
                part.grams = to_decimal(request.POST.get("grams"))
                part.print_minutes = int(request.POST.get("print_minutes") or 0)
                part.printer = printer
                part.quantity = to_decimal(request.POST.get("quantity"), "1")
                part.save()
                composition.calculated_at = None
                composition.save(update_fields=["calculated_at", "updated_at"])
            elif action == "part_delete":
                part = get_object_or_404(ManufacturingPart, pk=request.POST.get("part_id"), company=request.company, item__composition=composition, active=True)
                part.active = False
                part.save(update_fields=["active", "updated_at"])
                composition.calculated_at = None
                composition.save(update_fields=["calculated_at", "updated_at"])
            elif action == "supply":
                item = get_object_or_404(CompositionItem, pk=request.POST.get("item"), company=request.company, composition=composition, active=True)
                supply = get_object_or_404(Supply, pk=request.POST.get("supply"), company=request.company, active=True)
                use = optional_company_record(CompositionSupply, request.POST.get("use_id"), request.company, item__composition=composition, active=True) or CompositionSupply(company=request.company)
                use.item = item
                use.supply = supply
                use.quantity = to_decimal(request.POST.get("quantity"), "1")
                use.save()
                composition.calculated_at = None
                composition.save(update_fields=["calculated_at", "updated_at"])
            elif action == "supply_delete":
                use = get_object_or_404(CompositionSupply, pk=request.POST.get("use_id"), company=request.company, item__composition=composition, active=True)
                use.active = False
                use.save(update_fields=["active", "updated_at"])
                composition.calculated_at = None
                composition.save(update_fields=["calculated_at", "updated_at"])
            composition.recalculate()
            totals = {key: str(getattr(composition, key)) for key in ["material_cost", "energy_cost", "maintenance_cost", "depreciation_cost", "labor_cost", "supplies_cost", "base_calculation", "direct_cost", "margin_base", "margin_value", "suggested_price", "predicted_profit"]}
            if request.POST.get("preview") == "1":
                transaction.set_rollback(True)
                return JsonResponse({"ok": True, "preview": True, "totals": totals})
            if hasattr(composition, "order"):
                complete_order_calculation(composition.order)
            if hasattr(composition, "product"):
                product = composition.product
                product.current_cost = composition.direct_cost
                product.current_price = composition.suggested_price
                product.save(update_fields=["current_cost", "current_price", "updated_at"])
            if request.headers.get("X-Requested-With") == "XMLHttpRequest" and action == "meta":
                return JsonResponse({"ok": True, "totals": totals})
            messages.success(request, "Composição atualizada.")
            return redirect("composition", pk=composition.pk)
        except (ValidationError, ValueError) as exc:
            transaction.set_rollback(True)
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return JsonResponse({"error": "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)}, status=400)
            messages.error(request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
            return redirect("composition", pk=pk)
    context = common_context(request.company)
    context.update({
        "title": "Composição e precificação", "composition": composition,
        "calculation_models": CalculationModel.objects.filter(company=request.company, active=True),
        "active_items": composition.items.filter(active=True).prefetch_related("parts__material_family", "parts__printer", "supplies__supply"),
    })
    return render(request, "erp/composition.html", context)


@require_POST
@company_required
def composition_calculate(request, pk):
    composition = get_object_or_404(Composition, pk=pk, company=request.company, active=True)
    try:
        if hasattr(composition, "order"):
            from .services import validate_order_composition_edit
            validate_order_composition_edit(composition.order)
        composition.recalculate()
        if hasattr(composition, "order"):
            complete_order_calculation(composition.order)
        if hasattr(composition, "product"):
            product = composition.product
            product.current_cost = composition.direct_cost
            product.current_price = composition.suggested_price
            product.save(update_fields=["current_cost", "current_price", "updated_at"])
        messages.success(request, "Cálculo concluído e snapshot salvo.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("composition", pk=pk)


def pdf_color(value, fallback="#1769e8"):
    try:
        return colors.HexColor(value or fallback)
    except Exception:
        return colors.HexColor(fallback)


def pdf_styles(company=None):
    primary = pdf_color(company.primary_color if company else None)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="BrandTitle", parent=styles["Title"], textColor=primary, fontSize=18, leading=22))
    styles.add(ParagraphStyle(name="DocumentTitle", parent=styles["Title"], textColor=primary, fontSize=20, leading=23, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="SmallRight", parent=styles["BodyText"], alignment=TA_RIGHT, fontSize=8))
    styles.add(ParagraphStyle(name="TableText", parent=styles["BodyText"], fontSize=8, leading=10))
    styles.add(ParagraphStyle(name="Tiny", parent=styles["BodyText"], fontSize=7, leading=9))
    styles.add(ParagraphStyle(name="Section", parent=styles["Heading3"], textColor=primary, fontSize=10, leading=12, spaceAfter=4))
    styles.add(ParagraphStyle(name="TableHeader", parent=styles["TableText"], textColor=colors.white, fontName="Helvetica-Bold"))
    return styles


def pdf_brl(value):
    formatted = f"{Decimal(value or 0):,.2f}"
    return "R$ " + formatted.replace(",", "_").replace(".", ",").replace("_", ".")


def pdf_qty(value):
    return format(Decimal(value or 0).normalize(), "f").replace(".", ",")


def company_pdf_snapshot(company):
    return {
        "name": company.name, "trading_name": company.trading_name, "document": company.document,
        "responsible_name": company.responsible_name, "slogan": company.slogan, "phone": company.phone,
        "whatsapp": company.whatsapp, "email": company.email, "instagram": company.instagram,
        "website": company.website, "address": company.address, "city": company.city, "state": company.state,
        "primary_color": company.primary_color, "secondary_color": company.secondary_color,
        "success_color": company.success_color, "warning_color": company.warning_color,
        "state_registration": company.state_registration, "postal_code": company.postal_code,
        "logo": company.logo.name if company.logo else "",
    }


def company_pdf_header(company, styles, width, title=""):
    try:
        brand = Image(company.logo.path, width=45 * mm, height=24 * mm, kind="proportional") if company.logo else Paragraph(escape(str(company)), styles["BrandTitle"])
    except Exception:
        brand = Paragraph(escape(str(company)), styles["BrandTitle"])
    brand = [brand, Paragraph(escape(str(company)), styles["Section"]), Paragraph(escape(company.slogan), styles["Tiny"])]
    address = " - ".join(filter(None, [company.address, f"{company.city}/{company.state}" if company.city else "", company.postal_code]))
    identity_lines = [
        company.name, f"CNPJ/CPF: {company.document}" if company.document else "",
        f"Responsável: {company.responsible_name}" if company.responsible_name else "", address,
        " | ".join(dict.fromkeys(filter(None, [company.phone, company.whatsapp, company.email]))),
        " | ".join(filter(None, [company.instagram, company.website])),
    ]
    identity = Paragraph("<br/>".join(escape(line) for line in identity_lines if line), styles["TableText"])
    if title:
        table = Table([[brand, identity, Paragraph(title, styles["DocumentTitle"])]], colWidths=[width * .32, width * .40, width * .28])
    else:
        table = Table([[brand, identity]], colWidths=[width * .35, width * .65])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, pdf_color(company.primary_color)),
        ("LINEABOVE", (0, 0), (-1, -1), 1.5, pdf_color(company.secondary_color)),
    ]))
    return table


def build_pdf_bytes(filename, title, company, headers, rows, summary=None, pagesize=landscape(A4)):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=pagesize, rightMargin=14 * mm, leftMargin=14 * mm, topMargin=12 * mm, bottomMargin=12 * mm, title=title, author=str(company))
    styles = pdf_styles(company)
    usable_width = pagesize[0] - 28 * mm
    story = [company_pdf_header(company, styles, usable_width), Spacer(1, 5 * mm), Paragraph(escape(title).replace("—", "-"), styles["Section"]), Spacer(1, 3 * mm)]
    data = [[Paragraph(escape(str(cell)), styles["TableHeader"]) for cell in headers]]
    data.extend([[Paragraph(escape(str(cell)), styles["TableText"]) for cell in row] for row in rows])
    table = Table(data, colWidths=[usable_width / len(headers)] * len(headers), repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(company.primary_color)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dbe4f0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fc")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    if summary:
        story.extend([Spacer(1, 5 * mm), Paragraph(escape(summary), styles["Section"])])

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(14 * mm, 8 * mm, f"Gerado em {timezone.localtime().strftime('%d/%m/%Y %H:%M')}")
        canvas.drawRightString(pagesize[0] - 14 * mm, 8 * mm, f"Página {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def archived_pdf_response(company, document_type, reference_id, filename, builder, snapshot=None, attachment=False):
    snapshot = json.loads(json.dumps(snapshot or {}, default=str))
    version = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:20]
    reference_id = f"{reference_id}:{version}"
    issued = IssuedDocument.objects.filter(company=company, document_type=document_type, reference_id=str(reference_id)).first()
    if issued:
        content = bytes(issued.content)
        filename = issued.filename
    else:
        content = builder()
        issued, _ = IssuedDocument.objects.get_or_create(
            company=company, document_type=document_type, reference_id=str(reference_id), filename=filename,
            defaults={"content": content, "content_hash": hashlib.sha256(content).hexdigest(), "snapshot": snapshot},
        )
        content = bytes(issued.content)
    response = FileResponse(io.BytesIO(content), as_attachment=attachment, filename=filename, content_type="application/pdf")
    response["Content-Length"] = len(content)
    return response


@company_required
def issued_document_pdf(request, pk):
    document = get_object_or_404(IssuedDocument, pk=pk, company=request.company)
    return FileResponse(io.BytesIO(bytes(document.content)), filename=document.filename, content_type="application/pdf")


def quote_pdf_bytes(quote):
    company = quote.company
    styles = pdf_styles(company)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=10 * mm, leftMargin=10 * mm, topMargin=9 * mm, bottomMargin=12 * mm, title=quote.code, author=str(company))
    width = A4[0] - 20 * mm
    number = quote.code.split("-")[-1].lstrip("0") or "0"
    title = f"ORÇAMENTO<br/><font size='10'>Orçamento - {int(number):04d}<br/>Data: {timezone.localtime(quote.created_at):%d/%m/%Y}</font>"
    story = [company_pdf_header(company, styles, width, title), Spacer(1, 4 * mm)]
    customer = quote.customer
    client_data = [
        ["DADOS DO CLIENTE", ""],
        [f"Nome / empresa: {customer.legal_name or customer.name}", f"CPF / CNPJ: {customer.document or '—'}"],
        [f"Cidade: {' / '.join(filter(None, [customer.city, customer.state])) or '—'}", f"Telefone: {customer.phone or customer.whatsapp or '—'}"],
        [f"E-mail: {customer.email or '—'}", f"Responsável: {customer.name}"],
    ]
    client_data = [[Paragraph(escape(str(cell)), styles["TableHeader"] if index == 0 else styles["TableText"]) for cell in row] for index, row in enumerate(client_data)]
    client_table = Table(client_data, colWidths=[width / 2, width / 2])
    client_table.setStyle(TableStyle([
        ("SPAN", (0, 0), (1, 0)), ("BACKGROUND", (0, 0), (1, 0), pdf_color(company.primary_color)),
        ("TEXTCOLOR", (0, 0), (1, 0), colors.white), ("FONTNAME", (0, 0), (1, 0), "Helvetica-Bold"),
        ("BOX", (0, 0), (-1, -1), .7, pdf_color(company.primary_color)), ("INNERGRID", (0, 1), (-1, -1), .25, colors.HexColor("#cbd5e1")),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend([client_table, Spacer(1, 4 * mm)])
    items = list(quote.composition.items.filter(active=True))
    weights = []
    for item in items:
        cost = sum((to_decimal(part.get("snapshot_cost")) for part in quote.composition.snapshot.get("parts", []) if part.get("item") == item.name), Decimal("0"))
        cost += sum((to_decimal(use.get("unit_cost")) * to_decimal(use.get("quantity")) for use in quote.composition.snapshot.get("supplies", []) if use.get("item") == item.name), Decimal("0"))
        weights.append(cost if cost > 0 else item.quantity)
    total_weight = sum(weights, Decimal("0")) or Decimal("1")
    item_budget = money(quote.manual_value - quote.freight_amount)
    allocated = Decimal("0")
    rows = [["ITEM", "DESCRIÇÃO DO PRODUTO / SERVIÇO", "QTD.", "UN.", "VALOR UNIT.", "VALOR TOTAL"]]
    for index, item in enumerate(items, 1):
        line_total = money(item_budget * weights[index - 1] / total_weight) if index < len(items) else item_budget - allocated
        allocated += line_total
        unit_value = money(line_total / item.quantity) if item.quantity else Decimal("0")
        rows.append([f"{index:02d}", Paragraph(f"<b>{escape(item.name)}</b><br/><font size='7'>{escape(item.description or '')}</font>", styles["TableText"]), str(item.quantity.normalize()), item.unit, pdf_brl(unit_value), pdf_brl(line_total)])
    for index in range(len(items) + 1, max(7, len(items) + 1)):
        rows.append([f"{index:02d}", "", "", "", "", ""])
    items_table = Table(rows, colWidths=[12 * mm, 82 * mm, 17 * mm, 14 * mm, 30 * mm, 35 * mm], repeatRows=1)
    items_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), pdf_color(company.primary_color)), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#b7c4d6")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 1), (-1, -1), 8), ("ALIGN", (0, 1), (0, -1), "CENTER"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"), ("TOPPADDING", (0, 1), (-1, -1), 5), ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
    ]))
    story.extend([items_table, Spacer(1, 4 * mm)])
    subtotal = money(quote.manual_value - quote.freight_amount)
    valid_days = max(0, (quote.valid_until - timezone.localdate()).days) if quote.valid_until else 0
    valid_until_text = quote.valid_until.strftime("%d/%m/%Y") if quote.valid_until else "data não informada"
    important = Paragraph(
        f"<b>INFORMAÇÕES IMPORTANTES</b><br/>• Este orçamento é válido por {valid_days} dia(s), até {valid_until_text}.<br/>"
        "• O prazo de produção e entrega será confirmado após a aprovação.<br/>• A produção começa após a confirmação do pagamento.<br/>"
        f"• Condições: {escape(quote.payment_terms or 'A combinar.')}<br/>• Valores dos itens distribuídos proporcionalmente ao orçamento aprovado.", styles["Tiny"],
    )
    totals = Table([
        ["SUBTOTAL", pdf_brl(subtotal)],
        ["FRETE", pdf_brl(quote.freight_amount)], ["TOTAL GERAL", pdf_brl(quote.manual_value)],
    ], colWidths=[42 * mm, 38 * mm])
    totals.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#aebbd0")), ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("BACKGROUND", (0, -1), (-1, -1), pdf_color(company.primary_color)),
        ("TEXTCOLOR", (0, -1), (-1, -1), colors.white), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(Table([[important, totals]], colWidths=[width - 84 * mm, 80 * mm], style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (0, 0), .5, colors.HexColor("#b7c4d6")), ("LEFTPADDING", (0, 0), (0, 0), 6), ("TOPPADDING", (0, 0), (0, 0), 6)])))
    methods = ", ".join(quote.company.paymentmethod_set.filter(active=True).values_list("name", flat=True)) or "A combinar"
    story.extend([
        Spacer(1, 4 * mm), Paragraph("OBSERVAÇÕES", styles["Section"]),
        Table([[Paragraph(escape(quote.notes or "Sem observações adicionais."), styles["TableText"])]], colWidths=[width], style=TableStyle([("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#b7c4d6")), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 12)])),
        Spacer(1, 3 * mm), Paragraph(f"<b>FORMAS DE PAGAMENTO:</b> {methods}", styles["TableText"]), Spacer(1, 8 * mm),
        Table([["________________________________", "________________________________"], ["ASSINATURA DO CLIENTE", f"ASSINATURA {str(company).upper()}"]], colWidths=[width / 2, width / 2], style=TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("FONTSIZE", (0, 0), (-1, -1), 8)])),
        Spacer(1, 4 * mm), Paragraph(escape("  |  ".join(filter(None, [company.instagram, company.whatsapp or company.phone, company.website]))), styles["SmallRight"]),
    ])
    doc.build(story)
    return buffer.getvalue()


@company_required
def quote_pdf(request, pk):
    quote = get_object_or_404(Quote.objects.select_related("company", "customer", "composition"), pk=pk, company=request.company, active=True)
    if not quote.composition.is_calculated or quote.manual_value is None or quote.manual_value < 0:
        messages.error(request, "Calcule os itens e informe o valor final manual antes de gerar o PDF.")
        return redirect(f"/orcamentos/?selected={quote.pk}")
    filename = f"orcamento-{quote.code}.pdf"
    snapshot = {"company": company_pdf_snapshot(quote.company), "quote": quote.code, "manual_value": str(quote.manual_value), "composition": quote.composition.snapshot, "notes": quote.notes, "payment_terms": quote.payment_terms, "freight": str(quote.freight_amount), "valid_until": str(quote.valid_until), "customer": {field: str(getattr(quote.customer, field)) for field in ["name", "legal_name", "document", "phone", "whatsapp", "email", "city", "state"]}}
    return archived_pdf_response(quote.company, "quote", quote.pk, filename, lambda: quote_pdf_bytes(quote), snapshot)


@company_required
def report_pdf(request, report_type):
    company = request.company
    start = parse_date(request.GET.get("start"), timezone.localdate().replace(day=1))
    end = parse_date(request.GET.get("end"), timezone.localdate())
    if report_type == "vendas":
        items = Sale.objects.filter(company=company, active=True, created_at__date__range=(start, end)).select_related("customer")
        rows = [[item.code, timezone.localtime(item.created_at).strftime("%d/%m/%Y"), item.customer.name, pdf_brl(item.gross_amount), pdf_brl(item.cost_amount), pdf_brl(item.profit_amount)] for item in items]
        filename, title, headers = "relatorio-vendas.pdf", f"Vendas de {start:%d/%m/%Y} a {end:%d/%m/%Y}", ["Código", "Data", "Cliente", "Faturamento", "Custo", "Lucro"]
        return archived_pdf_response(company, "report_sales", f"{start}:{end}", filename, lambda: build_pdf_bytes(filename, title, company, headers, rows), {"company": company_pdf_snapshot(company), "start": str(start), "end": str(end), "rows": rows})
    if report_type == "financeiro":
        items = FinancialEntry.objects.filter(company=company, active=True, issue_date__range=(start, end)).select_related("account")
        rows = [[item.code, item.issue_date.strftime("%d/%m/%Y"), item.get_direction_display(), item.description, item.account.name, item.get_status_display(), pdf_brl(item.net_amount)] for item in items]
        filename, title, headers = "relatorio-financeiro.pdf", f"Financeiro de {start:%d/%m/%Y} a {end:%d/%m/%Y}", ["Código", "Data", "Tipo", "Descrição", "Conta", "Status", "Valor"]
        return archived_pdf_response(company, "report_finance", f"{start}:{end}", filename, lambda: build_pdf_bytes(filename, title, company, headers, rows), {"company": company_pdf_snapshot(company), "start": str(start), "end": str(end), "rows": rows})
    items = Order.objects.filter(company=company, active=True, created_at__date__range=(start, end)).select_related("customer")
    rows = [[item.code, timezone.localtime(item.created_at).strftime("%d/%m/%Y"), item.customer.name, item.description, item.get_calculation_status_display(), item.get_financial_status_display(), pdf_brl(item.value)] for item in items]
    filename, title, headers = "relatorio-pedidos.pdf", f"Pedidos de {start:%d/%m/%Y} a {end:%d/%m/%Y}", ["Código", "Data", "Cliente", "Descrição", "Cálculo", "Financeiro", "Valor"]
    return archived_pdf_response(company, "report_orders", f"{start}:{end}", filename, lambda: build_pdf_bytes(filename, title, company, headers, rows), {"company": company_pdf_snapshot(company), "start": str(start), "end": str(end), "rows": rows})


@company_required
def customer_statement_pdf(request, pk):
    customer = get_object_or_404(Customer, pk=pk, company=request.company, active=True)
    rows = []
    total = Decimal("0")
    orders = list(customer.orders.filter(active=True, cancelled_at__isnull=True))
    order_ids = [str(order.pk) for order in orders]
    entries = customer.financial_entries.filter(active=True, direction="in", status="pending").exclude(source_type="erp.Order", source_id__in=order_ids).order_by("due_date")
    for entry in entries:
        rows.append([entry.code, entry.description, pdf_brl(entry.gross_amount), pdf_brl(0), pdf_brl(entry.gross_amount), entry.due_date.strftime("%d/%m/%Y"), entry.get_status_display()])
        total += entry.gross_amount
    for order in orders:
        if order.balance <= 0:
            continue
        pending_title = customer.financial_entries.filter(active=True, direction="in", status="pending", source_type="erp.Order", source_id=str(order.pk)).order_by("due_date").first()
        due = pending_title.due_date.strftime("%d/%m/%Y") if pending_title else "Não informado"
        rows.append([order.code, order.description, pdf_brl(order.value), pdf_brl(order.received), pdf_brl(order.balance), due, order.get_financial_status_display()])
        total += order.balance
    filename = f"extrato-{customer.pk}.pdf"
    customer_details = " | ".join(filter(None, [customer.legal_name or customer.name, customer.document, customer.phone or customer.whatsapp, customer.email]))
    title = f"Extrato de saldos em aberto - {customer_details}"
    fingerprint = hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return archived_pdf_response(request.company, "customer_statement", f"{customer.pk}:{fingerprint}", filename, lambda: build_pdf_bytes(filename, title, request.company, ["Documento", "Descrição", "Valor original", "Recebido", "Saldo", "Vencimento", "Status"], rows, f"TOTAL DA DÍVIDA EM ABERTO: {pdf_brl(total)}", A4), {"company": company_pdf_snapshot(request.company), "customer": customer.pk, "customer_details": customer_details, "total": str(total), "date": str(timezone.localdate())})


@company_required
def shipment_pdf(request, pk):
    shipment = get_object_or_404(
        ConsignmentShipment.objects.select_related("store").prefetch_related("items__product"),
        pk=pk, company=request.company, active=True,
    )
    rows = []
    total_units = Decimal("0")
    total_value = Decimal("0")
    for item in shipment.items.filter(active=True):
        line_total = money(item.quantity * item.reference_price)
        total_units += item.quantity
        total_value += line_total
        rows.append([
            item.product.sku, item.product.name, pdf_qty(item.quantity), pdf_brl(item.reference_price),
            f"{pdf_qty(item.commission_percent)}%", pdf_brl(line_total),
        ])
    filename = f"remessa-{shipment.code}.pdf"
    title = f"Remessa consignada — {shipment.code} — {shipment.store.name}"
    summary = f"Total de unidades: {pdf_qty(total_units)} · Valor de referência: {pdf_brl(total_value)} · Data: {shipment.shipment_date:%d/%m/%Y}"
    snapshot = {
        "company": company_pdf_snapshot(request.company), "shipment": shipment.code,
        "store": shipment.store.name, "units": str(total_units), "reference_value": str(total_value), "rows": rows, "date": str(shipment.shipment_date), "notes": shipment.notes,
    }
    return archived_pdf_response(
        request.company, "consignment_shipment", shipment.pk, filename,
        lambda: build_pdf_bytes(filename, title, request.company, ["SKU", "Produto", "Qtd.", "Preço ref.", "Comissão", "Valor total"], rows, summary, A4),
        snapshot,
    )


@company_required
def consignment_pdf(request, pk):
    settlement = get_object_or_404(ConsignmentSettlement.objects.prefetch_related("items__product"), pk=pk, company=request.company, active=True)
    sold_rows = [[item.product.name, pdf_qty(item.sold_quantity), pdf_brl(item.reference_price), pdf_brl(item.gross_amount), pdf_brl(item.commission_amount), pdf_brl(item.net_amount)] for item in settlement.items.all() if item.sold_quantity]
    remaining_items = [item for item in settlement.items.all() if item.found_quantity and item.disposition == "remain"]
    remaining_rows = [[item.product.name, pdf_qty(item.found_quantity), pdf_brl(item.reference_price), pdf_brl(item.found_quantity * item.reference_price)] for item in remaining_items]
    remaining_units = sum((item.found_quantity for item in remaining_items), Decimal("0"))
    remaining_value = sum((item.found_quantity * item.reference_price for item in remaining_items), Decimal("0"))
    filename = f"consignacao-{settlement.code}.pdf"
    def builder():
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=12 * mm, leftMargin=12 * mm, topMargin=10 * mm, bottomMargin=12 * mm, title=settlement.code, author=str(request.company))
        styles = pdf_styles(request.company)
        width = A4[0] - 24 * mm
        story = [company_pdf_header(request.company, styles, width, f"PRESTAÇÃO DE CONTAS<br/><font size='9'>{settlement.code}</font>"), Spacer(1, 4 * mm), Paragraph(f"Loja: {escape(settlement.store.name)} &nbsp;&nbsp; Período: {escape(settlement.period_reference)} &nbsp;&nbsp; Data: {settlement.settlement_date:%d/%m/%Y}", styles["TableText"]), Spacer(1, 4 * mm), Paragraph("PRODUTOS VENDIDOS", styles["Section"])]
        def styled_table(headers, rows, widths):
            data = [headers] + (rows or [["Sem itens", "", "", "", "", ""][:len(headers)]])
            data = [[Paragraph(escape(str(cell)), styles["TableHeader"] if index == 0 else styles["TableText"]) for cell in row] for index,row in enumerate(data)]
            table = Table(data, colWidths=widths, repeatRows=1)
            table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), pdf_color(request.company.primary_color)), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#b7c4d6")), ("FONTSIZE", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
            return table
        story.append(styled_table(["Produto", "Qtd.", "Preço", "Bruto", "Comissão", "Líquido"], sold_rows, [55 * mm, 15 * mm, 25 * mm, 28 * mm, 28 * mm, 30 * mm]))
        story.extend([
            Spacer(1, 3 * mm), Paragraph(f"<b>Totais vendidos:</b> {pdf_qty(settlement.units_sold)} un. &nbsp; <b>Bruto:</b> {pdf_brl(settlement.gross_amount)} &nbsp; <b>Comissão:</b> {pdf_brl(settlement.commission_amount)} &nbsp; <b>Líquido:</b> {pdf_brl(settlement.net_amount)}", styles["TableText"]),
            Spacer(1, 4 * mm), Paragraph("PERMANECEM CONSIGNADOS", styles["Section"]),
            styled_table(["Produto", "Qtd.", "Preço ref.", "Valor total"], remaining_rows, [85 * mm, 20 * mm, 36 * mm, 40 * mm]),
            Spacer(1, 3 * mm), Paragraph(f"<b>Totais remanescentes:</b> {pdf_qty(remaining_units)} un. &nbsp; <b>Valor do estoque consignado:</b> {pdf_brl(remaining_value)}", styles["TableText"]),
        ])
        doc.build(story)
        return buffer.getvalue()
    return archived_pdf_response(request.company, "consignment", settlement.pk, filename, builder, {"company": company_pdf_snapshot(request.company), "settlement": settlement.code, "sold": sold_rows, "remaining": remaining_rows, "store": settlement.store.name, "period": settlement.period_reference})
