"""Eventos estruturados; o ator vem da requisição, nunca do formulário."""
from contextvars import ContextVar
import uuid

from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from . import models as m

current_actor = ContextVar("erp_actor", default=None)


def related_records(obj):
    order = obj if isinstance(obj, m.Order) else getattr(obj, "order", None)
    if isinstance(obj, m.ProductionFailure):
        order = obj.demand.order
    if isinstance(obj, m.Composition):
        order = getattr(obj, "order", None)
    if isinstance(obj, m.FinancialEntry) and obj.source_type == "erp.Order" and str(obj.source_id).isdigit():
        order = m.Order.objects.filter(company=obj.company, pk=obj.source_id).first()
    request = obj if isinstance(obj, m.QuoteRequest) else getattr(obj, "request", None)
    if order:
        request = order.request
    customer = obj if isinstance(obj, m.Customer) else getattr(obj, "customer", None)
    if order:
        customer = order.customer
    if request:
        customer = request.customer
    if isinstance(obj, m.IssuedDocument):
        pk = obj.reference_id.split(":", 1)[0]
        if pk.isdigit() and obj.document_type == "customer_statement":
            customer = m.Customer.objects.filter(company=obj.company, pk=pk).first()
        elif pk.isdigit() and obj.document_type == "quote":
            quote = m.Quote.objects.filter(company=obj.company, pk=pk).first()
            if quote: request, customer = quote.request, quote.customer
    return order, request, customer


def log_event(obj, kind, description, user=None, *, key=None, details=None, at=None):
    order, request, customer = related_records(obj)
    actor = user or current_actor.get()
    if actor and not actor.is_authenticated:
        actor = None
    values = dict(kind=kind, description=description, user=actor, order=order,
        request=request, customer=customer, source_model=obj._meta.label,
        source_id=obj.pk, details=details or {})
    if at:
        values["happened_at"] = at
    event, _ = m.ActivityEvent.objects.get_or_create(company=obj.company,
        event_key=key or str(uuid.uuid4()), defaults=values)
    return event


TRACKED = {
    m.Customer: ("customer", "Cliente"), m.QuoteRequest: ("request", "Solicitação"),
    m.Quote: ("quote", "Orçamento"), m.Order: ("order", "Pedido"),
    m.ProductionDemand: ("production", "Demanda"), m.ProductionFailure: ("failure", "Falha"),
    m.Payment: ("payment", "Pagamento"), m.Sale: ("sale", "Venda PDV"),
    m.Purchase: ("purchase", "Compra"), m.FinancialEntry: ("finance", "Título financeiro"),
    m.ConsignmentShipment: ("shipment", "Remessa"), m.ConsignmentSettlement: ("settlement", "Prestação"),
    m.IssuedDocument: ("document", "Documento emitido"),
}
FIELDS = ["status", "stage", "deadline", "priority_level", "calculation_status", "financial_status", "delivered_at", "cancelled_at", "completed_at", "reserved_supplies", "active", "printer_id"]


@receiver(pre_save)
def remember_previous(sender, instance, raw=False, **kwargs):
    if raw or sender not in TRACKED:
        return
    fields = [field for field in FIELDS if hasattr(instance, field)]
    instance._activity_previous = sender.objects.filter(pk=instance.pk).values(*fields).first() if instance.pk else None


@receiver(post_save)
def record_changes(sender, instance, created, raw=False, **kwargs):
    if raw or sender not in TRACKED:
        return
    kind, label = TRACKED[sender]
    code = getattr(instance, "code", getattr(instance, "name", str(instance.pk)))
    if created:
        log_event(instance, f"{kind}.created", f"{label} {code} criado(a)", key=f"created:{instance._meta.label}:{instance.pk}", at=instance.created_at)
    else:
        previous = getattr(instance, "_activity_previous", None) or {}
        changes = {field: [str(old), str(getattr(instance, field))] for field, old in previous.items() if old != getattr(instance, field)}
        if changes:
            labels = {"status": "Status", "stage": "Etapa", "deadline": "Prazo", "priority_level": "Prioridade", "calculation_status": "Cálculo", "financial_status": "Pagamento", "delivered_at": "Entrega", "cancelled_at": "Cancelamento", "completed_at": "Conclusão", "reserved_supplies": "Reserva de insumos", "active": "Ativo", "printer_id": "Impressora"}
            phrases = []
            for field in changes:
                display = getattr(instance, f"get_{field}_display", None)
                value = display() if display else str(getattr(instance, field) or "—")
                phrases.append(f"{labels[field]}: {value}")
            log_event(instance, f"{kind}.updated", f"{label} {code} · " + "; ".join(phrases), details=changes)
    if sender is m.QuoteRequest and instance.reminder_at:
        reminder, new = m.RequestReminder.objects.get_or_create(request=instance, defaults={
            "company": instance.company, "original_at": instance.reminder_at, "scheduled_at": instance.reminder_at})
        if new:
            log_event(instance, "reminder.created", "Lembrete agendado", key=f"reminder:{reminder.pk}:created", details={"original_at": instance.reminder_at.isoformat()})
    if sender is m.QuoteRequest and instance.status == "cancelled":
        from .reminders import finish_reminder
        reminder = m.RequestReminder.objects.filter(request=instance).first()
        if reminder:
            finish_reminder(reminder, mode="cancelled")
    if sender in {m.Quote, m.Order} and created and instance.request_id:
        from .reminders import finish_reminder
        purpose = "quote" if sender is m.Quote else "order"
        reminder = m.RequestReminder.objects.filter(request_id=instance.request_id, purpose=purpose).first()
        if reminder:
            finish_reminder(reminder, mode=purpose)
