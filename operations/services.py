from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from core.models import Sequence, IdempotencyKey
from .models import Quote, Order, ProductionDemand

@transaction.atomic
def convert_quote(quote: Quote, key: str):
    idem, created = IdempotencyKey.objects.get_or_create(company=quote.company, key=key, operation='convert_quote')
    if not created and idem.result_id: return Order.objects.get(pk=idem.result_id)
    order = Order.objects.create(company=quote.company, code=Sequence.next(quote.company, 'PED'), customer=quote.customer,
        request=quote.request, quote=quote, value=quote.final_price, predicted_cost=quote.predicted_cost,
        calculation_status='completed', snapshot=quote.cost_snapshot)
    ProductionDemand.objects.create(company=quote.company, code=order.code, order=order, quantity=1, stage='art')
    quote.status = 'converted'; quote.save(update_fields=['status'])
    idem.result_id = str(order.pk); idem.save(update_fields=['result_id'])
    return order

@transaction.atomic
def advance_demand(demand, stage):
    if stage in ('queue','printing') and demand.order and demand.order.calculation_status != 'completed':
        raise ValidationError('Conclua o cálculo antes de avançar para impressão.')
    demand.stage = stage
    if stage == 'ready': demand.ready_at = timezone.now()
    demand.save(update_fields=['stage','ready_at','updated_at'])
    return demand
