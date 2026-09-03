from decimal import Decimal
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import (
    Alert, CalculationModel, Company, Composition, CompositionItem, CompositionSupply,
    ConsignedStore, ConsignmentBalance, Customer, Filament, FinancialAccount,
    ManufacturingPart, MaterialFamily, Membership, PaymentMethod, Printer, Product,
    ProductionDemand, Quote, QuoteRequest, Supply, ConsignmentShipment, ConsignmentShipmentItem,
    IssuedDocument, Payment, StockMovement, Sequence, Purchase, PurchaseItem, FinancialEntry, MaterialMovement,
)
from .services import (
    advance_demand, create_sale, create_settlement, move_product_stock,
    quote_to_order, register_production_failure, request_to_direct_order,
    clone_composition, complete_settlement, record_order_payment, complete_purchase, correct_purchase,
)


class ERPFlowTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Praise Test", slug="praise-test", energy_rate="1.00", labor_hour_rate="30",
            fixed_cost_per_order="2", waste_percent="5", default_margin_percent="40",
        )
        self.user = get_user_model().objects.create_user("tester", password="safe-test-pass")
        Membership.objects.create(company=self.company, user=self.user, role="admin")
        self.customer = Customer.objects.create(company=self.company, name="Cliente")
        self.model = CalculationModel.objects.create(company=self.company, name="Padrão", margin_percent="40", default=True)
        self.family = MaterialFamily.objects.create(company=self.company, name="PLA", reference_cost_kg="80", manual_cost_kg="80", last_cost_kg="80", weighted_cost_kg="80")
        self.printer = Printer.objects.create(company=self.company, name="A1", acquisition_cost="3500", useful_life_hours=10000, residual_percent="10", power_watts=300)
        self.supply = Supply.objects.create(company=self.company, name="Argola", unit="un", unit_cost="0.50", physical_stock="1", minimum_stock="2")
        self.filament = Filament.objects.create(company=self.company, family=self.family, color="Preto", unit_cost="80", closed_rolls=2, open_rolls=0, minimum_rolls=1)
        self.method = PaymentMethod.objects.create(company=self.company, name="PIX", kind="pix", fee_percent=0)
        self.account = FinancialAccount.objects.create(company=self.company, name="Banco", opening_balance=0)

    def composition(self, name="Produto"):
        composition = Composition.objects.create(company=self.company, name=name, calculation_model=self.model, labor_minutes=10)
        item = CompositionItem.objects.create(company=self.company, composition=composition, name=name, quantity=1, unit="un")
        ManufacturingPart.objects.create(company=self.company, item=item, name="Corpo", material_family=self.family, grams="100", print_minutes=60, printer=self.printer, quantity=1)
        CompositionSupply.objects.create(company=self.company, item=item, supply=self.supply, quantity=2)
        return composition

    def quote(self):
        composition = self.composition()
        composition.recalculate()
        request_item = QuoteRequest.objects.create(company=self.company, code=f"SOL-{Quote.objects.count()+1}", customer=self.customer, description="Produto & teste", origin="whatsapp")
        return Quote.objects.create(company=self.company, code=f"ORC-{Quote.objects.count()+1}", request=request_item, customer=self.customer, composition=composition, manual_value=100)

    def test_preview_uses_engine_without_persisting_item(self):
        quote = self.quote()
        self.client.force_login(self.user)
        item = quote.composition.items.get()
        response = self.client.post(f"/composicao/{quote.composition_id}/", {"action":"item_update", "item":item.pk, "name":"Prévia", "quantity":"3", "unit":"un", "preview":"1"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["preview"])
        self.assertGreater(Decimal(response.json()["totals"]["base_calculation"]), quote.composition.base_calculation)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 1)
        self.assertEqual(item.name, "Produto")

    def test_invalid_composition_quantity_does_not_mutate(self):
        quote = self.quote()
        self.client.force_login(self.user)
        count = quote.composition.items.count()
        response = self.client.post(f"/composicao/{quote.composition_id}/", {"action":"item", "name":"Inválido", "quantity":"-1"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(quote.composition.items.count(), count)

    def test_clone_preserves_cost_snapshot_and_excludes_inactive_items(self):
        composition = self.composition()
        composition.recalculate()
        old_cost = composition.base_calculation
        CompositionItem.objects.create(company=self.company, composition=composition, name="Excluído", quantity=10, active=False)
        self.family.reference_cost_kg = 800
        self.family.save()
        clone = clone_composition(composition)
        self.assertEqual(clone.base_calculation, old_cost)
        self.assertEqual(clone.items.count(), 1)
        self.assertEqual(clone.snapshot["parts"][0]["part_id"], clone.items.get().parts.get().pk)

    def test_zero_margin_is_respected(self):
        self.model.margin_percent = 0
        self.model.save()
        composition = self.composition()
        composition.recalculate()
        self.assertEqual(composition.suggested_price, composition.base_calculation)
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/parametros/"), 'name="margin" value="0,000"')

    def test_composition_overrides_do_not_change_defaults_or_manual_value(self):
        quote = self.quote()
        self.client.force_login(self.user)
        self.model.tax_percent = 10
        self.model.save()
        response = self.client.post(f"/composicao/{quote.composition_id}/", {
            "action": "meta", "calculation_model": self.model.pk, "labor_minutes": "10",
            "discount": "0", "margin_override": "100", "waste_override": "0",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        quote.refresh_from_db()
        composition = quote.composition
        self.assertEqual(composition.margin_value, composition.margin_base)
        self.assertEqual(composition.suggested_price, composition.base_calculation + composition.margin_base)
        self.assertEqual(Decimal(composition.snapshot["components"]["waste"]), 0)
        self.assertEqual(quote.manual_value, 100)
        self.model.refresh_from_db()
        self.company.refresh_from_db()
        self.assertEqual(self.model.margin_percent, 40)
        self.assertEqual(self.company.waste_percent, 5)
        copied = clone_composition(composition)
        self.assertEqual(copied.margin_override, 100)
        self.assertEqual(copied.waste_override, 0)

    def test_purchase_correction_preserves_paid_entry_and_creates_pending_difference(self):
        purchase = Purchase.objects.create(company=self.company, code="COM-TEST", supplier="Fornecedor",
            payment_method=self.method, account=self.account, total=Decimal("10"))
        PurchaseItem.objects.create(company=self.company, purchase=purchase, supply=self.supply,
            quantity=Decimal("10"), unit_cost=Decimal("1"), total=Decimal("10"))
        complete_purchase(purchase, self.account, self.user)
        original = FinancialEntry.objects.get(source_type="erp.Purchase", source_id=str(purchase.pk))
        original.status = "paid"
        original.save()
        correct_purchase(purchase, "8", "1", "Conferência da nota", self.user)
        original.refresh_from_db()
        self.supply.refresh_from_db()
        self.assertEqual(original.status, "paid")
        self.assertEqual(original.gross_amount, Decimal("10"))
        self.assertEqual(self.supply.physical_stock, Decimal("9"))
        correction = FinancialEntry.objects.get(source_type="erp.PurchaseCorrection", source_id=str(purchase.pk))
        self.assertEqual(correction.direction, "in")
        self.assertEqual(correction.gross_amount, Decimal("2"))
        self.assertEqual(correction.status, "pending")
        self.assertIsNone(correction.paid_at)
        self.assertEqual(MaterialMovement.objects.filter(source_type="erp.Purchase", source_id=str(purchase.pk)).count(), 2)

    def test_customer_statement_excludes_paid_orders_and_does_not_duplicate_pending_titles(self):
        order = quote_to_order(self.quote(), uuid.uuid4())
        record_order_payment(order, self.method, self.account, 20, uuid.uuid4())
        pending = record_order_payment(order, self.method, self.account, 80, uuid.uuid4(), received=False)
        paid_order = quote_to_order(self.quote(), uuid.uuid4())
        record_order_payment(paid_order, self.method, self.account, 100, uuid.uuid4())
        self.client.force_login(self.user)
        with patch("erp.views.build_pdf_bytes", return_value=b"%PDF-TEST") as builder:
            response = self.client.get(f"/clientes/{self.customer.pk}/extrato.pdf")
        self.assertEqual(response.status_code, 200)
        rows = builder.call_args.args[4]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], order.code)
        self.assertEqual(rows[0][4], "R$ 80,00")
        self.assertEqual(rows[0][5], pending.financial_entry.due_date.strftime("%d/%m/%Y"))
        self.assertIn("R$ 80,00", builder.call_args.args[5])

    def test_fee_does_not_leave_customer_with_false_debt(self):
        order = quote_to_order(self.quote(), uuid.uuid4())
        self.method.fee_percent = Decimal("5")
        self.method.save()
        record_order_payment(order, self.method, self.account, 100, uuid.uuid4())
        order.refresh_from_db()
        self.assertEqual(order.balance, 0)
        self.assertEqual(order.financial_status, "paid")
        self.assertEqual(order.payments.get().net_amount, Decimal("95.00"))

    def test_ready_is_idempotent_for_supply_deduction(self):
        composition = self.composition()
        product = Product.objects.create(company=self.company, name="Reposição", sku="REP", composition=composition)
        demand = ProductionDemand.objects.create(company=self.company, code="REP-55", origin="replenishment", product=product, quantity=1, stage="printing")
        advance_demand(demand, "ready", self.user)
        advance_demand(demand, "ready", self.user)
        self.supply.refresh_from_db()
        self.assertEqual(self.supply.physical_stock, Decimal("-1"))
        self.assertEqual(StockMovement.objects.filter(product=product).count(), 1)

    def test_shipment_is_atomic_and_post_is_idempotent(self):
        self.client.force_login(self.user)
        store = ConsignedStore.objects.create(company=self.company, name="Loja", default_commission_percent=15)
        product = Product.objects.create(company=self.company, name="Produto", sku="SHIP", current_stock=2, current_price=10)
        data = {"store":store.pk, "date":"2026-09-02", "product":[product.pk], "quantity":[3], "price":[10], "commission":[0], "confirm":"1", "idempotency_key":str(uuid.uuid4())}
        self.client.post("/consignacao/remessas/nova/", data)
        self.assertEqual(ConsignmentShipment.objects.count(), 0)
        product.refresh_from_db()
        self.assertEqual(product.current_stock, 2)
        data["quantity"] = [1]
        self.client.post("/consignacao/remessas/nova/", data)
        self.client.post("/consignacao/remessas/nova/", data)
        self.assertEqual(ConsignmentShipment.objects.count(), 1)
        self.assertEqual(ConsignmentShipmentItem.objects.get().commission_percent, 0)
        product.refresh_from_db()
        self.assertEqual(product.current_stock, 1)

    def test_settlement_return_is_traceable_and_cannot_repeat(self):
        product = Product.objects.create(company=self.company, name="Consignado", sku="CON")
        store = ConsignedStore.objects.create(company=self.company, name="Loja", default_commission_percent=15)
        ConsignmentBalance.objects.create(company=self.company, store=store, product=product, quantity=5, reference_price=10)
        settlement = create_settlement(self.company, store, {str(product.pk):3}, "09/2026")
        item = settlement.items.get()
        item.disposition = "return"
        item.save()
        complete_settlement(settlement, self.account, self.method, self.user)
        complete_settlement(settlement, self.account, self.method, self.user)
        product.refresh_from_db()
        self.assertEqual(product.current_stock, 3)
        self.assertEqual(StockMovement.objects.filter(product=product).count(), 1)

    def test_pdf_new_version_preserves_old_bytes(self):
        quote = self.quote()
        self.client.force_login(self.user)
        first = self.client.get(f"/orcamentos/{quote.pk}.pdf")
        first_bytes = b"".join(first.streaming_content)
        original = IssuedDocument.objects.get()
        self.company.name = "Nova razão social & parceiros"
        self.company.save()
        second = self.client.get(f"/orcamentos/{quote.pk}.pdf")
        second_bytes = b"".join(second.streaming_content)
        self.assertEqual(IssuedDocument.objects.count(), 2)
        self.assertNotEqual(first_bytes, second_bytes)
        archived = self.client.get(f"/documentos/{original.pk}.pdf")
        self.assertEqual(b"".join(archived.streaming_content), first_bytes)

    def test_store_can_be_edited_and_quote_has_modal_controls(self):
        self.client.force_login(self.user)
        store = ConsignedStore.objects.create(company=self.company, name="Loja", default_commission_percent=15)
        self.client.post("/consignacao/", {"action":"store", "store_id":store.pk, "name":"Loja revisada", "commission":20})
        store.refresh_from_db()
        self.assertEqual(store.name, "Loja revisada")
        quote = self.quote()
        response = self.client.get(f"/orcamentos/?selected={quote.pk}")
        self.assertContains(response, 'data-open-composition=')
        self.assertContains(response, 'data-quote-margin')

    def test_quick_customer_and_item_update(self):
        self.client.force_login(self.user)
        response = self.client.post('/clientes/rapido/', {'name':'Cliente rápido', 'phone':'123'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Customer.objects.get(pk=response.json()['id']).name, 'Cliente rápido')
        quote = self.quote()
        item = quote.composition.items.get()
        response = self.client.post(f'/composicao/{quote.composition_id}/', {'action':'item_update','item':item.pk,'name':'Editado','quantity':'2','unit':'un'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)
        quote.refresh_from_db()
        self.assertEqual(quote.manual_value, 100)

    def test_sku_sequence_skips_manual_numbers_and_never_reuses_inactive(self):
        Product.objects.create(company=self.company,name='Manual',sku='0001',active=False)
        self.assertEqual(Sequence.next_numeric(self.company), '0002')
        self.assertEqual(Sequence.next_numeric(self.company), '0003')

    def test_production_has_material_stage_but_no_color_field(self):
        stages = {value for value, _ in ProductionDemand.STAGES}
        self.assertEqual(stages, {"art", "material", "queue", "printing", "ready"})
        field_names = {field.name for field in ManufacturingPart._meta.fields}
        self.assertNotIn("color", field_names)

    def test_direct_order_is_blocked_until_calculation(self):
        request_item = QuoteRequest.objects.create(company=self.company, code="SOL-00001", customer=self.customer, description="Peça", origin="whatsapp")
        order = request_to_direct_order(request_item, uuid.uuid4())
        demand = order.demands.get()
        with self.assertRaises(ValidationError):
            advance_demand(demand, "queue", self.user)
        order.composition.items.all().delete()
        source = self.composition("Peça")
        for source_item in source.items.all():
            source_item.composition = order.composition
            source_item.save()
        order.composition.recalculate()
        order.calculation_status = "completed"
        order.save()
        advance_demand(demand, "material", self.user)
        with self.assertRaises(ValidationError):
            advance_demand(demand, "queue", self.user)
        self.supply.physical_stock = 3
        self.supply.save(update_fields=["physical_stock"])
        advance_demand(demand, "queue", self.user)
        demand.refresh_from_db()
        self.assertEqual(demand.stage, "queue")

    def test_ready_deducts_supplies_but_not_filament(self):
        composition = self.composition()
        product = Product.objects.create(company=self.company, name="Produto", sku="P-1", composition=composition, minimum_stock=1, target_stock=5, current_cost=10, current_price=20)
        demand = ProductionDemand.objects.create(company=self.company, code="REP-00001", origin="replenishment", product=product, item_name=product.name, quantity=1, stage="printing")
        advance_demand(demand, "ready", self.user)
        self.supply.refresh_from_db()
        self.filament.refresh_from_db()
        self.assertEqual(self.supply.physical_stock, Decimal("-1.000"))
        self.assertEqual(self.filament.closed_rolls, 2)
        self.assertTrue(Alert.objects.filter(company=self.company, level="critical").exists())

    def test_pos_is_atomic_and_updates_stock_and_finance(self):
        composition = self.composition()
        product = Product.objects.create(company=self.company, name="Produto", sku="P-2", composition=composition, minimum_stock=1, target_stock=5, current_cost=10, current_price=25)
        move_product_stock(product, 5, "adjustment", self.user)
        sale = create_sale(self.company, self.customer, self.method, [(product, 2)], self.account, uuid.uuid4(), self.user)
        product.refresh_from_db()
        self.assertEqual(product.current_stock, Decimal("3.000"))
        self.assertEqual(sale.gross_amount, Decimal("50.00"))
        self.assertEqual(self.account.entries.filter(status="paid", direction="in").count(), 1)

    def test_consignment_negative_sale_is_blocked_as_divergence(self):
        composition = self.composition()
        product = Product.objects.create(company=self.company, name="Produto", sku="P-3", composition=composition, minimum_stock=1, target_stock=5, current_cost=10, current_price=25)
        store = ConsignedStore.objects.create(company=self.company, name="Loja", default_commission_percent=15)
        ConsignmentBalance.objects.create(company=self.company, store=store, product=product, quantity=5, reference_price=25, commission_percent=15)
        settlement = create_settlement(self.company, store, {str(product.pk): "7"}, "08/2026")
        item = settlement.items.get()
        self.assertEqual(settlement.status, "blocked")
        self.assertEqual(item.sold_quantity, Decimal("0.000"))
        self.assertTrue(item.divergence)

    def test_three_pricing_bases_are_independent(self):
        rules = self.model.normalized_rules()
        rules["labor"] = {"calculate": True, "cost": False, "margin": False}
        self.model.component_rules = rules
        self.model.margin_percent = Decimal("50")
        self.model.save(update_fields=["component_rules", "margin_percent"])
        composition = self.composition()
        values = composition.recalculate()
        self.assertGreater(values["base_calculation"], values["direct_cost"])
        self.assertEqual(values["margin_value"], values["margin_base"] * Decimal("0.50"))
        self.assertEqual(values["suggested_price"], values["base_calculation"] + values["margin_value"])

    def test_quote_order_uses_manual_commercial_value(self):
        request_item = QuoteRequest.objects.create(
            company=self.company, code="SOL-00002", customer=self.customer,
            description="Peça comercial", origin="whatsapp",
        )
        composition = self.composition("Peça comercial")
        composition.recalculate()
        quote = Quote.objects.create(
            company=self.company, code="ORC-00001", request=request_item,
            customer=self.customer, composition=composition, manual_value=Decimal("199.90"),
        )
        order = quote_to_order(quote, uuid.uuid4())
        self.assertEqual(order.value, Decimal("199.90"))
        self.assertEqual(order.predicted_cost, composition.direct_cost)

    def test_failure_cost_comes_from_calculation_snapshot(self):
        composition = self.composition("Peça com falha")
        composition.recalculate()
        request_item = QuoteRequest.objects.create(
            company=self.company, code="SOL-00003", customer=self.customer,
            description="Peça com falha", origin="balcao",
        )
        quote = Quote.objects.create(
            company=self.company, code="ORC-00002", request=request_item,
            customer=self.customer, composition=composition, manual_value=Decimal("150.00"),
        )
        order = quote_to_order(quote, uuid.uuid4())
        demand = order.demands.get()
        part = order.composition.items.get().parts.get()
        snapshot_cost = Decimal(next(row["snapshot_cost"] for row in order.composition.snapshot["parts"] if row["part_id"] == part.pk))
        failure = register_production_failure(demand, part, Decimal("25"), "Erro de impressão")
        self.assertEqual(failure.additional_cost, (snapshot_cost * Decimal("0.25")).quantize(Decimal("0.01")))
        order.refresh_from_db()
        self.assertEqual(order.actual_cost, order.predicted_cost + failure.additional_cost)

    def test_company_isolation_and_main_pages(self):
        other = Company.objects.create(name="Outra", slug="outra")
        Product.objects.create(company=self.company, name="Um", sku="SAME", minimum_stock=0, target_stock=0)
        Product.objects.create(company=other, name="Dois", sku="SAME", minimum_stock=0, target_stock=0)
        self.client.login(username="tester", password="safe-test-pass")
        for url in ["/", "/solicitacoes/", "/orcamentos/", "/pedidos/", "/producao/", "/catalogo/", "/estoque/", "/materiais/", "/clientes/", "/vendas/nova/", "/consignacao/", "/financeiro/", "/relatorios/", "/parametros/"]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertNotContains(response, "Outra")
