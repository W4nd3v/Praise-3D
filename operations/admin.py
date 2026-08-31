from django.contrib import admin
from .models import QuoteRequest, Quote, QuoteItem, Order, ProductionDemand, ProductionFailure
admin.site.register([QuoteRequest, Quote, QuoteItem, Order, ProductionDemand, ProductionFailure])

# Register your models here.
