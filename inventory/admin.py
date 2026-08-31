from django.contrib import admin
from .models import Printer, CalculationModel, Material, MaterialMovement, Category, Product, StockMovement
admin.site.register([Printer, CalculationModel, Material, MaterialMovement, Category, Product, StockMovement])

# Register your models here.
