from django.db import migrations


def backfill(apps, schema_editor):
    alias = schema_editor.connection.alias
    Event = apps.get_model("erp", "ActivityEvent")
    Reminder = apps.get_model("erp", "RequestReminder")
    Order = apps.get_model("erp", "Order")
    Order.objects.using(alias).filter(priority=True, priority_level="normal").update(priority_level="priority")
    Demand = apps.get_model("erp", "ProductionDemand")
    legacy_priority_orders = Demand.objects.using(alias).filter(priority=True, order_id__isnull=False).values("order_id")
    Order.objects.using(alias).filter(pk__in=legacy_priority_orders, priority_level="normal").update(priority=True, priority_level="priority")
    order_lookup = {o.pk: (o.request_id, o.customer_id) for o in Order.objects.using(alias).all().iterator()}
    for name, prefix, label in [("Customer", "customer", "Cliente"), ("QuoteRequest", "request", "Solicitação"),
            ("Quote", "quote", "Orçamento"), ("Order", "order", "Pedido"), ("ProductionDemand", "production", "Produção"),
            ("Sale", "sale", "Venda"), ("Payment", "payment", "Pagamento"), ("Purchase", "purchase", "Compra")]:
        Model = apps.get_model("erp", name)
        for obj in Model.objects.using(alias).all().iterator():
            order_id = obj.pk if name == "Order" else getattr(obj, "order_id", None)
            request_id = obj.pk if name == "QuoteRequest" else getattr(obj, "request_id", None)
            customer_id = obj.pk if name == "Customer" else getattr(obj, "customer_id", None)
            if order_id in order_lookup: request_id, customer_id = order_lookup[order_id]
            Event.objects.using(alias).get_or_create(company_id=obj.company_id, event_key=f"created:erp.{name}:{obj.pk}", defaults={
                "happened_at": obj.created_at, "kind": f"{prefix}.created", "description": f"{label} {getattr(obj, 'code', getattr(obj, 'name', obj.pk))} criado(a)",
                "order_id": order_id, "request_id": request_id, "customer_id": customer_id, "source_model": f"erp.{name}", "source_id": obj.pk,
                "details": {"migrated": True, "note": "Data original de cadastro. Autor histórico não disponível; sem inferir mudanças passadas."}})
            if name == "QuoteRequest" and obj.reminder_at:
                reminder, created = Reminder.objects.using(alias).get_or_create(request_id=obj.pk, defaults={"company_id": obj.company_id,
                    "original_at": obj.reminder_at, "scheduled_at": obj.reminder_at, "purpose": "manual",
                    "status": "cancelled" if obj.status == "cancelled" else "scheduled"})
                if created:
                    Event.objects.using(alias).get_or_create(company_id=obj.company_id, event_key=f"reminder:{reminder.pk}:created", defaults={
                        "happened_at": obj.created_at, "kind": "reminder.created", "description": "Lembrete preservado do cadastro anterior",
                        "request_id": obj.pk, "customer_id": obj.customer_id, "source_model": "erp.QuoteRequest", "source_id": obj.pk,
                        "details": {"migrated": True, "original_at": obj.reminder_at.isoformat()}})


class Migration(migrations.Migration):
    dependencies = [("erp", "0005_consignmentshipment_cancelled_at_and_more")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
