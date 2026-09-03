from datetime import timedelta
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from erp.models import (
    Alert, CalculationModel, Company, Composition, CompositionItem, CompositionSupply,
    ConsignedStore, ConsignmentShipment, ConsignmentShipmentItem, Customer, Filament,
    FinancialAccount, ManufacturingPart, MaterialFamily, Membership, Order, PaymentMethod,
    Printer, Product, ProductCategory, ProductType, ProductionDemand, QuoteRequest, Sequence, Supply,
)
from erp.services import (
    advance_demand, complete_order_calculation, complete_shipment, create_manual_entries,
    create_sale, move_product_stock, quote_to_order, record_order_payment,
    request_to_direct_order, request_to_quote,
)


class Command(BaseCommand):
    help = "Cria a empresa Praise 3D e um conjunto completo de dados demonstrativos."

    def handle(self, *args, **options):
        if Company.objects.filter(slug="praise-3d").exists():
            self.stdout.write(self.style.WARNING("Os dados de demonstração já existem; nenhuma duplicação foi feita."))
            return

        company = Company.objects.create(
            name="Praise 3D Soluções em Impressão",
            trading_name="Praise 3D",
            slug="praise-3d",
            slogan="A pioneira do 3D",
            document="59.703.841/0001-08",
            responsible_name="Maria Eduarda Bezerra Alves Amaro",
            phone="(88) 98121-4692", whatsapp="(88) 98121-4692", email="contato@praise3d.com.br",
            instagram="@praise3doficial", website="https://praise3d.com.br", city="Jaguaribe", state="CE",
            primary_color="#1769e8",
            secondary_color="#ffb21c",
            success_color="#16a263",
            energy_rate=Decimal("0.96"),
            labor_hour_rate=Decimal("28.00"),
            fixed_cost_per_order=Decimal("3.50"),
            waste_percent=Decimal("7.00"),
            default_margin_percent=Decimal("42.00"),
            default_filament_minimum=3,
        )
        User = get_user_model()
        user = User.objects.create_user(username="admin", email="admin@praise3d.local", password="Praise3D@2026", first_name="Josafá", last_name="Administrador")
        user.is_staff = True
        user.is_superuser = True
        user.save()
        Membership.objects.create(company=company, user=user, role="admin")

        model = CalculationModel.objects.create(
            company=company,
            name="Padrão Praise 3D",
            description="Modelo operacional padrão com bases independentes de preço, custo e margem.",
            pricing_method="margin",
            margin_percent=Decimal("42.00"),
            tax_percent=Decimal("0"),
            default=True,
        )
        printer_a1 = Printer.objects.create(company=company, name="Bambu Lab A1", model="A1", acquisition_cost=Decimal("3500"), useful_life_hours=12000, residual_percent=10, power_watts=350, maintenance_per_hour=Decimal("0.85"))
        printer_p1s = Printer.objects.create(company=company, name="Bambu Lab P1S", model="P1S", acquisition_cost=Decimal("7800"), useful_life_hours=15000, residual_percent=10, power_watts=420, maintenance_per_hour=Decimal("1.25"))

        families = {}
        for name, cost in [("PLA", "80"), ("PETG", "95"), ("ABS", "110"), ("TPU", "145"), ("Resina", "165")]:
            families[name] = MaterialFamily.objects.create(company=company, name=name, reference_cost_kg=cost, manual_cost_kg=cost, last_cost_kg=cost, weighted_cost_kg=cost)

        filament_specs = [
            ("PLA", "Preto", "#111111", 24, 3, 5, "79.90"),
            ("PLA", "Branco", "#f4f4f4", 12, 2, 4, "82.00"),
            ("PLA", "Azul", "#1565ff", 3, 2, 5, "85.00"),
            ("PETG", "Preto", "#1a1a1a", 9, 1, 3, "94.00"),
            ("PETG", "Vermelho", "#e62323", 4, 1, 3, "98.00"),
            ("ABS", "Preto", "#171717", 2, 1, 3, "112.00"),
            ("TPU", "Cinza", "#777777", 7, 0, 2, "142.00"),
        ]
        filaments = []
        for family, color, color_hex, closed, opened, minimum, cost in filament_specs:
            filaments.append(Filament.objects.create(company=company, family=families[family], color=color, color_hex=color_hex, brand="3D Fila", supplier="3D Fila Materiais", unit_cost=cost, closed_rolls=closed, open_rolls=opened, minimum_rolls=minimum))

        supply_specs = [
            ("Argola de chaveiro", "un", "0.38", "260", "50"),
            ("Ímã 8x3 mm", "un", "0.55", "180", "40"),
            ("Fita LED", "m", "9.80", "28", "8"),
            ("Cabo USB", "un", "8.50", "16", "5"),
            ("Embalagem kraft", "un", "1.25", "120", "30"),
            ("Parafuso M3", "un", "0.14", "450", "100"),
        ]
        supplies = {}
        for name, unit, cost, stock, minimum in supply_specs:
            supplies[name] = Supply.objects.create(company=company, name=name, unit=unit, supplier="Parceiro Industrial", unit_cost=cost, physical_stock=stock, minimum_stock=minimum)

        pix = PaymentMethod.objects.create(company=company, name="PIX", kind="pix", installments=1, fee_percent=0, days_to_receive=0)
        cash = PaymentMethod.objects.create(company=company, name="Dinheiro", kind="cash", installments=1, fee_percent=0, days_to_receive=0)
        credit1 = PaymentMethod.objects.create(company=company, name="Crédito 1x", kind="credit", installments=1, fee_percent=Decimal("2.49"), days_to_receive=30)
        PaymentMethod.objects.create(company=company, name="Crédito 2x", kind="credit", installments=2, fee_percent=Decimal("3.79"), days_to_receive=30)
        PaymentMethod.objects.create(company=company, name="Crédito 3x", kind="credit", installments=3, fee_percent=Decimal("4.99"), days_to_receive=30)
        PaymentMethod.objects.create(company=company, name="Crédito 6x", kind="credit", installments=6, fee_percent=Decimal("6.99"), days_to_receive=30)
        bank = FinancialAccount.objects.create(company=company, name="Banco do Brasil", kind="bank", opening_balance=Decimal("82540"))
        FinancialAccount.objects.create(company=company, name="Caixa", kind="cash", opening_balance=Decimal("1250"))

        anonymous = Customer.objects.create(company=company, name="Consumidor não identificado", anonymous=True)
        customer_data = [
            ("Paulo Silva", "(11) 99811-2020", "São Paulo", "SP"),
            ("Fernanda Silva", "(11) 98765-4321", "São Paulo", "SP"),
            ("Carlito Menezes", "(21) 99100-4433", "Rio de Janeiro", "RJ"),
            ("Ana Beatriz", "(31) 98812-7771", "Belo Horizonte", "MG"),
            ("Lucas Ferreira", "(19) 99902-1255", "Campinas", "SP"),
            ("Mayanni Artigos", "(11) 97731-4141", "Guarulhos", "SP"),
        ]
        customers = {}
        for name, phone, city, state in customer_data:
            customers[name] = Customer.objects.create(company=company, name=name, whatsapp=phone, phone=phone, email=f"{name.lower().replace(' ', '.')}@email.com", city=city, state=state)

        categories = {name: ProductCategory.objects.create(company=company, name=name) for name in ["Personalizados", "Utilitários", "Colecionáveis", "Brinquedos", "Brindes", "Decoração"]}
        product_types = {name: ProductType.objects.create(company=company, name=name) for name in ["Produto próprio", "Sob encomenda", "Serviço"]}

        def product(name, sku, category, stock, minimum, target, family, grams, minutes, price_hint, supply=None):
            composition = Composition.objects.create(company=company, name=name, calculation_model=model, labor_minutes=12)
            item = CompositionItem.objects.create(company=company, composition=composition, name=name, quantity=1, unit="un")
            ManufacturingPart.objects.create(company=company, item=item, name="Corpo principal", material_family=families[family], grams=grams, print_minutes=minutes, printer=printer_a1, quantity=1)
            if supply:
                CompositionSupply.objects.create(company=company, item=item, supply=supplies[supply], quantity=1)
            composition.recalculate()
            prod = Product.objects.create(company=company, name=name, sku=sku, category=category, category_ref=categories[category], product_type=product_types["Produto próprio"], description=f"{name} produzido sob controle de composição.", composition=composition, minimum_stock=minimum, target_stock=target, current_cost=composition.direct_cost, current_price=max(composition.suggested_price, Decimal(str(price_hint))))
            move_product_stock(prod, Decimal(str(stock)), "adjustment", user, None, "Estoque inicial demonstrativo")
            return prod

        products = [
            product("Boneco personalizado 20 cm", "PROD-0158", "Personalizados", 32, 10, 50, "PLA", 210, 360, 120),
            product("Suporte de parede", "PROD-0210", "Utilitários", 12, 8, 40, "PETG", 72, 110, 35),
            product("Labubu - Edição especial", "PROD-0333", "Colecionáveis", 5, 6, 30, "PLA", 260, 480, 220),
            product("Carrinho articulado", "PROD-0401", "Brinquedos", 7, 5, 25, "PLA", 86, 125, 85),
            product("Argola de chaveiro 3D", "PROD-0502", "Brindes", 22, 20, 60, "PLA", 16, 28, 15, "Argola de chaveiro"),
            product("Vaso decorativo", "PROD-0610", "Decoração", 3, 5, 20, "PLA", 145, 240, 68),
        ]

        request_specs = [
            (customers["Paulo Silva"], "Máscara Iron Man", "new", "whatsapp"),
            (customers["Fernanda Silva"], "Cofrinho personalizado", "new", "instagram"),
            (customers["Carlito Menezes"], "Suporte para controle", "analysis", "whatsapp"),
            (customers["Ana Beatriz"], "Organizador de mesa", "waiting", "referral"),
            (customers["Lucas Ferreira"], "Vaso decorativo personalizado", "new", "store"),
        ]
        requests = []
        for customer, description, status, origin in request_specs:
            requests.append(QuoteRequest.objects.create(company=company, code=Sequence.next(company, "SOL"), customer=customer, description=description, origin=origin, status=status, reminder_at=timezone.now() + timedelta(days=1) if status != "waiting" else None))

        quote = request_to_quote(requests[1], uuid.uuid4())
        quote_item = quote.composition.items.first()
        ManufacturingPart.objects.create(company=company, item=quote_item, name="Corpo", material_family=families["PLA"], grams=185, print_minutes=280, printer=printer_a1, quantity=1)
        quote.composition.labor_minutes = 35
        quote.composition.discount_percent = Decimal("5")
        quote.composition.save()
        quote.composition.recalculate()
        quote.manual_value = quote.composition.suggested_price
        quote.status = "waiting"
        quote.save(update_fields=["manual_value", "status", "updated_at"])
        converted = quote_to_order(quote, uuid.uuid4(), timezone.localdate() + timedelta(days=7))

        direct = request_to_direct_order(requests[2], uuid.uuid4(), timezone.localdate() + timedelta(days=5))
        direct_demand = direct.demands.first()
        direct_demand.priority = True
        direct_demand.save()

        other_quote = request_to_quote(requests[4], uuid.uuid4())
        other_item = other_quote.composition.items.first()
        ManufacturingPart.objects.create(company=company, item=other_item, name="Vaso", material_family=families["PLA"], grams=170, print_minutes=310, printer=printer_p1s, quantity=1)
        other_quote.composition.recalculate()
        other_quote.manual_value = other_quote.composition.suggested_price
        other_quote.save(update_fields=["manual_value", "updated_at"])
        other_order = quote_to_order(other_quote, uuid.uuid4(), timezone.localdate() + timedelta(days=9))
        advance_demand(other_order.demands.first(), "material", user)
        advance_demand(other_order.demands.first(), "queue", user)
        advance_demand(other_order.demands.first(), "printing", user)

        record_order_payment(converted, pix, bank, converted.value / 2, uuid.uuid4(), True, "Entrada de 50%")
        create_sale(company, anonymous, pix, [(products[4], 2), (products[1], 1)], bank, uuid.uuid4(), user)
        create_sale(company, customers["Mayanni Artigos"], credit1, [(products[3], 1)], bank, uuid.uuid4(), user)

        create_manual_entries(company, "in", "Recebimento NF 1538", "Recebimento de vendas", "1980", bank, pix, timezone.localdate() - timedelta(days=2), True, customer=customers["Mayanni Artigos"])
        create_manual_entries(company, "out", "Energia elétrica", "Despesas fixas", "680", bank, pix, timezone.localdate() - timedelta(days=3), True, supplier="CEB Distribuição")
        create_manual_entries(company, "out", "Aluguel", "Despesas fixas", "2200", bank, pix, timezone.localdate() + timedelta(days=5), False, supplier="Imobiliária Central")

        store = ConsignedStore.objects.create(company=company, name="Loja Centro - São Paulo", contact_name="Mariana", phone="(11) 3100-4400", address="Centro, São Paulo - SP", default_commission_percent=15)
        shipment = ConsignmentShipment.objects.create(company=company, code=Sequence.next(company, "REM"), store=store)
        for prod, quantity in [(products[0], 8), (products[1], 6), (products[4], 10)]:
            ConsignmentShipmentItem.objects.create(company=company, shipment=shipment, product=prod, quantity=quantity, reference_price=prod.current_price, commission_percent=15, snapshot={"name": prod.name, "price": str(prod.current_price)})
        complete_shipment(shipment, user)

        Product.objects.filter(pk=products[2].pk).update(current_stock=5)
        Alert.objects.create(company=company, level="warning", title="Revisar orçamento ORC", message="Cliente aguarda retorno do orçamento convertido em demonstração.")
        self.stdout.write(self.style.SUCCESS("Dados criados com sucesso."))
        self.stdout.write("Acesso: admin / Praise3D@2026")
