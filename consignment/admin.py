from django.contrib import admin
from .models import ConsignmentStore, Shipment, ShipmentItem, Settlement, SettlementItem
admin.site.register([ConsignmentStore, Shipment, ShipmentItem, Settlement, SettlementItem])

# Register your models here.
