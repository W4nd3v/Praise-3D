from django.contrib import admin
from .models import FinancialAccount, PaymentMethod, FinancialEntry, Sale, SaleItem
admin.site.register([FinancialAccount, PaymentMethod, FinancialEntry, Sale, SaleItem])

# Register your models here.
