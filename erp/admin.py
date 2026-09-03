from django.contrib import admin
from . import models


admin.site.site_header = "Praise 3D - Administração"
admin.site.site_title = "Praise 3D"

for model in [
    models.Company, models.Membership, models.Customer, models.PaymentMethod,
    models.Printer, models.MaterialFamily, models.Filament, models.Supply,
    models.CalculationModel, models.CustomCostComponent, models.Composition,
    models.CompositionItem, models.ManufacturingPart, models.CompositionSupply,
    models.QuoteRequest, models.Quote, models.ProductCategory, models.ProductType, models.Product, models.Order,
    models.ProductionDemand, models.ProductionFailure, models.StockMovement,
    models.FinancialAccount, models.FinancialEntry, models.Payment, models.Sale,
    models.SaleItem, models.Purchase, models.PurchaseItem, models.MaterialMovement,
    models.ConsignedStore, models.ConsignmentBalance, models.ConsignmentShipment,
    models.ConsignmentShipmentItem, models.ConsignmentSettlement,
    models.ConsignmentSettlementItem, models.Alert, models.AuditLog, models.IssuedDocument,
]:
    admin.site.register(model)
