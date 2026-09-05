"""Encerramentos auditáveis: nunca apagam movimentos ou recebimentos antigos."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone
from . import models as m
from .activity import log_event
from .services import move_product_stock, release_demand_reservations, set_product_active


def reverse_entry(entry, reason, user=None):
    entry = m.FinancialEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.status == "cancelled": return
    if entry.status == "paid":
        # Cash already moved. A pending counter-entry requires explicit reconciliation.
        if not m.FinancialEntry.objects.filter(company=entry.company, source_type="erp.Reversal", source_id=str(entry.pk)).exists():
            m.FinancialEntry.objects.create(company=entry.company, code=m.Sequence.next(entry.company, "FIN"),
                direction="out" if entry.direction == "in" else "in", description=f"Estorno a conciliar · {entry.code}",
                category="Estornos", account=entry.account, customer=entry.customer, supplier=entry.supplier,
                payment_method=entry.payment_method, gross_amount=entry.gross_amount, net_amount=entry.gross_amount,
                source_type="erp.Reversal", source_id=str(entry.pk), status="pending", notes=reason,
                snapshot={"original_entry": entry.pk, "original_gross": str(entry.gross_amount), "original_fee": str(entry.fee_amount)})
        log_event(entry, "finance.reversal", "Estorno pendente de conciliação; recebimento original preservado", user, key=f"reversal:{entry.pk}")
    else:
        entry.status = "cancelled"
        entry.save(update_fields=["status", "updated_at"])
    payment = m.Payment.objects.filter(financial_entry=entry).first()
    if payment:
        payment.status = "cancelled"
        payment.save(update_fields=["status", "updated_at"])
        if payment.order_id:
            order = m.Order.objects.select_for_update().get(pk=payment.order_id)
            order.financial_status = "paid" if order.balance <= 0 else ("partial" if order.received else "pending")
            order.save(update_fields=["financial_status", "updated_at"])


@transaction.atomic
def cancel_record(obj, reason, user=None):
    if not reason.strip(): raise ValidationError("Informe o motivo do cancelamento.")
    obj = type(obj).objects.select_for_update().get(pk=obj.pk)
    if getattr(obj, "cancelled_at", None) or getattr(obj, "status", None) == "cancelled": return obj
    entries = m.FinancialEntry.objects.none()
    if isinstance(obj, m.Order):
        if obj.delivered_at: raise ValidationError("Pedido entregue não pode ser cancelado por esta ação. Registre a devolução e a conciliação separadamente.")
        for demand in obj.demands.select_for_update().filter(active=True):
            release_demand_reservations(demand)
            demand.active = False
            demand.save(update_fields=["active", "reserved_supplies", "updated_at"])
        # Completed production has consumed real material; cancellation does not recreate it.
        entries = m.FinancialEntry.objects.filter(company=obj.company, source_type="erp.Order", source_id=str(obj.pk))
    elif isinstance(obj, m.Sale):
        for item in obj.items.select_related("product"):
            move_product_stock(item.product, item.quantity, "return", user, obj, f"Cancelamento {obj.code}: {reason}"[:240])
        entries = m.FinancialEntry.objects.filter(pk=obj.financial_entry_id)
    elif isinstance(obj, m.Purchase):
        if obj.completed_at:
            for item in obj.items.all():
                material = (m.Filament if item.filament_id else m.Supply).objects.select_for_update().get(pk=item.filament_id or item.supply_id)
                if item.filament_id:
                    if material.closed_rolls < item.quantity: raise ValidationError("Rolos da compra já foram abertos/consumidos; cancelamento bloqueado.")
                    material.closed_rolls -= int(item.quantity)
                else:
                    if material.available_stock < item.quantity: raise ValidationError("Há insumos consumidos ou reservados; cancelamento bloqueado.")
                    material.physical_stock -= item.quantity
                material.save()
                m.MaterialMovement.objects.create(company=obj.company, filament=item.filament, supply=item.supply, quantity=-item.quantity,
                    movement_type="adjustment", source_type="erp.Purchase", source_id=str(obj.pk), user=user, note=f"Cancelamento {obj.code}: {reason}"[:220])
        entries = m.FinancialEntry.objects.filter(company=obj.company, source_type__in=["erp.Purchase", "erp.PurchaseCorrection"], source_id=str(obj.pk))
        obj.status = "cancelled"
    elif isinstance(obj, m.ConsignmentShipment):
        if obj.completed_at:
            if obj.store.settlements.filter(status="completed", completed_at__gte=obj.completed_at).exists():
                raise ValidationError("Já existe prestação de contas posterior. Faça o retorno pela prestação para preservar os saldos.")
            for item in obj.items.select_related("product"):
                balance = m.ConsignmentBalance.objects.select_for_update().get(company=obj.company, store=obj.store, product=item.product)
                if balance.quantity < item.quantity: raise ValidationError("Saldo consignado insuficiente para retornar a remessa.")
                balance.quantity -= item.quantity
                balance.save(update_fields=["quantity", "updated_at"])
                move_product_stock(item.product, item.quantity, "consignment_return", user, obj, f"Cancelamento {obj.code}: {reason}"[:240])
    elif isinstance(obj, m.Quote):
        if obj.status == "converted" or m.Order.objects.filter(quote=obj).exists(): raise ValidationError("Orçamento convertido: gerencie o pedido vinculado.")
        obj.status = "cancelled"
    elif isinstance(obj, m.QuoteRequest):
        if m.Quote.objects.filter(request=obj).exclude(status="cancelled").exists() or m.Order.objects.filter(request=obj, cancelled_at__isnull=True).exists():
            raise ValidationError("Encerre os orçamentos/pedidos vinculados antes de cancelar a solicitação.")
        obj.status = "cancelled"
    elif isinstance(obj, m.FinancialEntry):
        reverse_entry(obj, reason, user)
        return obj
    else:
        raise ValidationError("Este cadastro permite inativação, não cancelamento.")
    for entry in entries: reverse_entry(entry, reason, user)
    if hasattr(obj, "cancelled_at"): obj.cancelled_at = timezone.now()
    obj.save()
    log_event(obj, "operation.cancelled", f"Cancelamento de {getattr(obj, 'code', obj.pk)}: {reason}", user, key=f"cancelled:{obj._meta.label}:{obj.pk}")
    return obj


@transaction.atomic
def set_record_active(obj, active, user=None):
    obj = type(obj).objects.select_for_update().get(pk=obj.pk)
    if isinstance(obj, m.Product):
        return set_product_active(obj, active, user)
    if not active:
        if isinstance(obj, m.Customer) and obj.orders.filter(cancelled_at__isnull=True, delivered_at__isnull=True).exists():
            raise ValidationError("Cliente com pedidos abertos: encerre-os antes de inativar.")
        if isinstance(obj, m.ConsignedStore) and obj.balances.filter(quantity__gt=0).exists():
            raise ValidationError("A loja ainda possui produtos consignados.")
        if isinstance(obj, m.Supply) and obj.reserved_stock > 0: raise ValidationError("Insumo com reservas de produção.")
        if isinstance(obj, m.FinancialAccount) and obj.is_default:
            raise ValidationError("Defina outra conta padrão antes de inativar esta conta.")
    obj.active = active
    obj.save(update_fields=["active", "updated_at"])
    log_event(obj, "record.active", "Cadastro reativado" if active else "Cadastro inativado", user)


def _has_operational_dependencies(obj):
    """Considera qualquer vínculo reverso, exceto a trilha de criação do próprio cadastro."""
    for relation in obj._meta.related_objects:
        if relation.related_model is m.ActivityEvent:
            continue
        try:
            related = getattr(obj, relation.get_accessor_name())
        except ObjectDoesNotExist:
            continue
        if hasattr(related, "exists"):
            if related.exists():
                return True
        elif related is not None:
            return True
    if isinstance(obj, m.Product) and obj.composition_id:
        composition = obj.composition
        if hasattr(composition, "quote") or hasattr(composition, "order"):
            return True
    return False


@transaction.atomic
def delete_or_inactivate(obj, reason, user=None):
    """Apaga cadastro nunca usado; havendo histórico, executa inativação auditável."""
    if not reason.strip():
        raise ValidationError("Informe o motivo da exclusão.")
    obj = type(obj).objects.select_for_update().get(pk=obj.pk)
    if _has_operational_dependencies(obj) or (isinstance(obj, m.Product) and (obj.current_stock or obj.consignment_balances.filter(quantity__gt=0).exists())):
        set_record_active(obj, False, user)
        log_event(obj, "record.delete.soft", f"Exclusão lógica: {reason}", user)
        return False
    label = getattr(obj, "name", None) or getattr(obj, "code", None) or str(obj)
    company, source_model, source_id = obj.company, obj._meta.label, obj.pk
    composition_id = obj.composition_id if isinstance(obj, m.Product) else None
    if isinstance(obj, m.Customer):
        m.ActivityEvent.objects.filter(company=company, customer=obj).delete()
    obj.delete()
    if composition_id:
        composition = m.Composition.objects.filter(pk=composition_id).first()
        if composition and not (
            m.Product.objects.filter(composition_id=composition_id).exists()
            or m.Quote.objects.filter(composition_id=composition_id).exists()
            or m.Order.objects.filter(composition_id=composition_id).exists()
        ):
            composition.delete()
    m.ActivityEvent.objects.create(
        company=company, kind="record.deleted", description=f"Cadastro {label} excluído definitivamente: {reason}",
        user=user, source_model=source_model, source_id=source_id, event_key=f"deleted:{source_model}:{source_id}",
        details={"label": label, "reason": reason, "mode": "physical"},
    )
    return True


@transaction.atomic
def archive_record(obj, archived, user=None):
    if archived:
        if isinstance(obj, m.Order) and not (obj.cancelled_at or (obj.delivered_at and obj.balance <= 0)):
            raise ValidationError("Encerre a entrega e o financeiro antes de arquivar o pedido.")
        if isinstance(obj, m.Quote) and obj.status not in {"converted", "expired", "cancelled"}: raise ValidationError("Orçamento ainda aberto.")
        if isinstance(obj, m.QuoteRequest) and obj.status not in {"ordered", "quoted", "cancelled"}: raise ValidationError("Solicitação ainda aberta.")
        if isinstance(obj, m.ProductionDemand) and obj.active and obj.stage != "ready": raise ValidationError("Produção ainda em andamento.")
        if isinstance(obj, m.FinancialEntry) and obj.status == "pending": raise ValidationError("Título financeiro ainda pendente.")
        if isinstance(obj, m.Purchase) and not obj.cancelled_at and (not obj.completed_at or m.FinancialEntry.objects.filter(company=obj.company, source_type__in=["erp.Purchase", "erp.PurchaseCorrection"], source_id=str(obj.pk), status="pending").exists()):
            raise ValidationError("Confirme a compra e encerre suas parcelas antes de arquivar.")
    m.ArchivedRecord.objects.update_or_create(company=obj.company, source_model=obj._meta.label, source_id=obj.pk,
        defaults={"archived": archived, "user": user, "archived_at": timezone.now()})
    log_event(obj, "record.archive", "Registro arquivado" if archived else "Registro desarquivado", user)
