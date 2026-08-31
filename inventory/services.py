from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from .models import Material, MaterialMovement, StockMovement

@transaction.atomic
def open_roll(material, key):
    material = Material.objects.select_for_update().get(pk=material.pk, company=material.company)
    if material.type != 'filament' or material.closed_rolls < 1: raise ValidationError('Não há rolo fechado disponível.')
    material.closed_rolls = F('closed_rolls') - 1; material.open_rolls = F('open_rolls') + 1
    material.save(update_fields=['closed_rolls','open_rolls'])
    MaterialMovement.objects.create(company=material.company, material=material, type='open', quantity=1, idempotency_key=key)

@transaction.atomic
def finish_roll(material, key):
    material = Material.objects.select_for_update().get(pk=material.pk, company=material.company)
    if material.open_rolls < 1: raise ValidationError('Não há rolo em uso.')
    material.open_rolls = F('open_rolls') - 1; material.save(update_fields=['open_rolls'])
    MaterialMovement.objects.create(company=material.company, material=material, type='finish', quantity=1, idempotency_key=key)

@transaction.atomic
def move_stock(product, quantity, movement_type, source_type, source_id, key):
    product = product.__class__.objects.select_for_update().get(pk=product.pk, company=product.company)
    new_balance = product.stock + Decimal(quantity)
    if new_balance < 0: raise ValidationError('Estoque insuficiente.')
    product.stock = new_balance; product.save(update_fields=['stock'])
    return StockMovement.objects.create(company=product.company, product=product, type=movement_type, quantity=quantity,
        source_type=source_type, source_id=source_id, idempotency_key=key, balance_after=new_balance)
