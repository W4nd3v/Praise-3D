from datetime import timedelta
from decimal import Decimal
import uuid

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from . import models as m
from . import services as s
from . import tests as baseline
from .lifecycle import delete_or_inactivate


class Correction4RulesTests(TestCase):
    """Regressões das regras operacionais introduzidas na Correção 4."""

    setUp = baseline.ERPFlowTests.setUp
    composition = baseline.ERPFlowTests.composition
    quote = baseline.ERPFlowTests.quote

    def order(self, value=Decimal("200.00")):
        quote = self.quote()
        quote.manual_value = value
        quote.save(update_fields=["manual_value", "updated_at"])
        return s.quote_to_order(quote, uuid.uuid4())

    def test_plate_quantity_rates_grams_and_time_without_rounding(self):
        self.company.energy_rate = Decimal("1")
        self.company.save(update_fields=["energy_rate", "updated_at"])
        self.family.reference_cost_kg = Decimal("100")
        self.family.save(update_fields=["reference_cost_kg", "updated_at"])
        self.printer.power_watts = 1000
        self.printer.acquisition_cost = 0
        self.printer.maintenance_per_hour = 0
        self.printer.save(update_fields=["power_watts", "acquisition_cost", "maintenance_per_hour", "updated_at"])
        composition = m.Composition.objects.create(
            company=self.company, name="Mesa rateada", calculation_model=self.model
        )
        item = m.CompositionItem.objects.create(
            company=self.company, composition=composition, name="Peça", quantity=10
        )
        m.ManufacturingPart.objects.create(
            company=self.company, item=item, name="Corpo", material_family=self.family,
            grams=100, print_minutes=120, printer=self.printer, quantity=1, plate_quantity=4,
        )

        composition.recalculate()

        self.assertEqual(composition.material_cost, Decimal("25.00"))
        self.assertEqual(composition.energy_cost, Decimal("5.00"))
        snapshot = composition.snapshot["parts"][0]
        self.assertEqual(Decimal(snapshot["rated_grams"]), Decimal("250"))
        self.assertEqual(Decimal(snapshot["rated_hours"]), Decimal("5"))
        self.assertEqual(snapshot["plate_quantity"], 4)

    def test_advance_and_final_receivables_are_exact_and_idempotent(self):
        self.company.require_order_advance = True
        self.company.order_advance_percent = Decimal("50")
        self.company.receivable_days_after_completion = 7
        self.company.save(update_fields=[
            "require_order_advance", "order_advance_percent",
            "receivable_days_after_completion", "updated_at",
        ])
        order = self.order()
        titles = m.FinancialEntry.objects.filter(source_type="erp.Order", source_id=str(order.pk))
        self.assertEqual(titles.count(), 1)
        self.assertEqual(titles.get().gross_amount, Decimal("100.00"))

        self.assertIsNone(s.ensure_order_advance_receivable(order, self.user))
        completed_on = timezone.localdate()
        final = s.ensure_order_final_receivable(order, completed_on, self.user)
        self.assertEqual(final.gross_amount, Decimal("100.00"))
        self.assertEqual(final.due_date, completed_on + timedelta(days=7))
        self.assertIsNone(s.ensure_order_final_receivable(order, completed_on, self.user))
        self.assertEqual(titles.filter(status="pending").count(), 2)
        self.assertEqual(
            titles.filter(status="pending").aggregate(total=Sum("gross_amount"))["total"],
            Decimal("200.00"),
        )

    def test_settled_advance_leaves_only_remaining_final_balance(self):
        self.company.require_order_advance = True
        self.company.order_advance_percent = Decimal("50")
        self.company.save(update_fields=["require_order_advance", "order_advance_percent", "updated_at"])
        self.account.is_default = True
        self.account.save(update_fields=["is_default", "updated_at"])
        order = self.order()
        advance = m.FinancialEntry.objects.get(
            source_type="erp.Order", source_id=str(order.pk), snapshot__purpose="advance"
        )

        s.settle_financial_entry(advance, None, self.method, self.user)
        final = s.ensure_order_final_receivable(order, user=self.user)

        order.refresh_from_db()
        self.assertEqual(order.received, Decimal("100.00"))
        self.assertEqual(final.gross_amount, Decimal("100.00"))
        self.assertEqual(order.financial_status, "partial")

    def test_ready_fully_paid_order_creates_no_pending_title(self):
        order = self.order()
        self.supply.physical_stock = 10
        self.supply.save(update_fields=["physical_stock", "updated_at"])
        s.record_order_payment(order, self.method, self.account, order.value, uuid.uuid4())
        s.advance_demand(order.demands.get(), "ready", self.user)

        self.assertFalse(m.FinancialEntry.objects.filter(
            source_type="erp.Order", source_id=str(order.pk), status="pending"
        ).exists())
        self.assertEqual(order.activity_events.filter(kind="order.ready.paid").count(), 1)

    def test_pending_entry_changes_balance_only_when_settled(self):
        self.account.active = False
        self.account.save(update_fields=["active", "updated_at"])
        self.client.force_login(self.user)
        response = self.client.post("/financeiro/", {
            "direction": "in", "description": "Pendente sem conta", "category": "Teste",
            "amount": "50", "account": "", "payment_method": "", "customer": "",
            "due_date": timezone.localdate().isoformat(), "installments": "1",
        })
        self.assertEqual(response.status_code, 302)
        entry = m.FinancialEntry.objects.get(description="Pendente sem conta")
        self.assertIsNone(entry.account)
        with self.assertRaises(ValidationError):
            s.settle_financial_entry(entry, None, self.method, self.user)

        account = m.FinancialAccount.objects.create(
            company=self.company, name="Caixa padrão", kind="cash", opening_balance=0, is_default=True
        )
        s.settle_financial_entry(entry, None, self.method, self.user)
        entry.refresh_from_db()
        self.assertEqual(entry.account, account)
        self.assertEqual(entry.status, "paid")
        self.assertEqual(account.balance, Decimal("50.00"))

    def test_manual_replenishment_accepts_any_positive_integer(self):
        product = m.Product.objects.create(
            company=self.company, name="Produto", sku="REP-FLEX", current_stock=9,
            minimum_stock=3, target_stock=10, operational_activity=False,
        )
        demand = s.create_replenishment(product, 25, self.user)
        self.assertEqual(demand.quantity, Decimal("25"))
        with self.assertRaises(ValidationError):
            s.create_replenishment(product, Decimal("1.5"), self.user)
        with self.assertRaises(ValidationError):
            s.create_replenishment(product, 0, self.user)

    def test_replenishment_removal_releases_reservations_and_blocks_effects(self):
        product = m.Product.objects.create(company=self.company, name="Produto", sku="REP-SAFE")
        demand = s.create_replenishment(product, 2, self.user)
        self.supply.reserved_stock = Decimal("1")
        self.supply.save(update_fields=["reserved_stock", "updated_at"])
        demand.reserved_supplies = {str(self.supply.pk): "1"}
        demand.save(update_fields=["reserved_supplies", "updated_at"])

        s.cancel_replenishment(demand, "Lançamento indevido", self.user)
        demand.refresh_from_db()
        self.supply.refresh_from_db()
        self.assertFalse(demand.active)
        self.assertEqual(self.supply.reserved_stock, 0)

        effected = s.create_replenishment(product, 1, self.user)
        m.MaterialMovement.objects.create(
            company=self.company, movement_type="production", supply=self.supply,
            quantity=-1, source_type="erp.ProductionDemand", source_id=str(effected.pk),
        )
        with self.assertRaises(ValidationError):
            s.cancel_replenishment(effected, "Tentar excluir", self.user)

        order_demand = self.order().demands.get()
        with self.assertRaises(ValidationError):
            s.cancel_replenishment(order_demand, "Não permitido", self.user)

    def test_product_inactivation_zeroes_every_location_and_reactivation_stays_zero(self):
        product = m.Product.objects.create(
            company=self.company, name="Produto", sku="INAT-1", current_stock=8
        )
        store = m.ConsignedStore.objects.create(
            company=self.company, name="Loja Centro", default_commission_percent=15
        )
        balance = m.ConsignmentBalance.objects.create(
            company=self.company, store=store, product=product, quantity=5,
            reference_price=20, commission_percent=15,
        )

        s.set_product_active(product, False, self.user)
        product.refresh_from_db()
        balance.refresh_from_db()
        self.assertFalse(product.active)
        self.assertEqual(product.current_stock, 0)
        self.assertEqual(balance.quantity, 0)
        movements = m.StockMovement.objects.filter(product=product).order_by("pk")
        self.assertEqual(movements.count(), 2)
        self.assertSetEqual(
            set(movements.values_list("location", flat=True)),
            {"Estoque central", "Consignação · Loja Centro"},
        )

        s.set_product_active(product, True, self.user)
        product.refresh_from_db()
        self.assertTrue(product.active)
        self.assertEqual(product.current_stock, 0)

    def test_operational_activity_only_controls_alerts(self):
        product = m.Product.objects.create(
            company=self.company, name="Produto pausado", sku="PAUSE", current_stock=0,
            minimum_stock=5, target_stock=10, operational_activity=False,
        )
        self.client.force_login(self.user)
        dashboard = self.client.get("/")
        stock = self.client.get("/estoque/")

        self.assertNotIn(product.pk, dashboard.context["products_low"].values_list("pk", flat=True))
        self.assertIn(product.pk, [item.pk for item in stock.context["stock_products"]])
        self.assertEqual(s.create_replenishment(product, 3, self.user).quantity, Decimal("3"))

    def test_multi_item_purchase_moves_each_line_and_creates_one_payable_total(self):
        second = m.Supply.objects.create(
            company=self.company, name="Embalagem", unit="un", unit_cost=2,
            physical_stock=0, reserved_stock=0, minimum_stock=0,
        )
        self.client.force_login(self.user)
        payload = {
            "action": "purchase", "idempotency_key": str(uuid.uuid4()),
            "material_type": "supply", "supplier": "Fornecedor dois itens",
            "purchase_date": "2026-09-04", "payment_method": str(self.method.pk),
            "account": "", "installments": "1", "first_due_date": "2026-09-10",
            "material_id": [str(self.supply.pk), str(second.pk)],
            "quantity": ["3", "4"], "unit_cost": ["2", "3"],
            "line_total": ["6", "12"], "save_status": "pending",
        }
        self.assertEqual(self.client.post("/materiais/", payload).status_code, 302)
        purchase = m.Purchase.objects.get(supplier="Fornecedor dois itens")
        self.assertEqual(purchase.status, "pending")
        self.assertEqual(purchase.items.count(), 2)
        self.assertFalse(m.MaterialMovement.objects.filter(source_type="erp.Purchase", source_id=str(purchase.pk)).exists())
        self.assertFalse(m.FinancialEntry.objects.filter(source_type="erp.Purchase", source_id=str(purchase.pk)).exists())

        self.assertEqual(self.client.post("/materiais/", {
            "action": "purchase_confirm", "purchase_id": purchase.pk,
        }).status_code, 302)
        purchase.refresh_from_db()
        self.supply.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(purchase.status, "effected")
        self.assertEqual(self.supply.physical_stock, Decimal("4"))
        self.assertEqual(second.physical_stock, Decimal("4"))
        self.assertEqual(m.MaterialMovement.objects.filter(
            source_type="erp.Purchase", source_id=str(purchase.pk)
        ).count(), 2)
        payable = m.FinancialEntry.objects.get(source_type="erp.Purchase", source_id=str(purchase.pk))
        self.assertEqual(payable.gross_amount, Decimal("18.00"))
        self.assertEqual(payable.status, "pending")
        self.assertIsNone(payable.account)

    def test_consignment_store_keeps_complete_address(self):
        self.client.force_login(self.user)
        response = self.client.post("/consignacao/lojas/", {
            "name": "Loja Completa", "contact_name": "Ana", "phone": "88999990000",
            "address": "Rua Azul", "street_number": "123", "district": "Centro",
            "complement": "Sala 2", "city": "Jaguaribe", "state": "ce",
            "postal_code": "63475000", "commission": "15", "notes": "Parceira",
        })
        self.assertEqual(response.status_code, 302)
        store = m.ConsignedStore.objects.get(name="Loja Completa")
        self.assertEqual(store.state, "CE")
        self.assertEqual(store.street_number, "123")
        self.assertEqual(store.district, "Centro")
        self.assertEqual(store.complement, "Sala 2")

    def test_safe_delete_is_physical_only_for_unused_catalog_record(self):
        unused = m.Supply.objects.create(
            company=self.company, name="Nunca usado", physical_stock=0,
            reserved_stock=0, minimum_stock=0, unit_cost=0,
        )
        unused_pk = unused.pk
        self.assertTrue(delete_or_inactivate(unused, "Cadastro duplicado", self.user))
        self.assertFalse(m.Supply.objects.filter(pk=unused_pk).exists())
        self.assertTrue(m.ActivityEvent.objects.filter(
            source_model="erp.Supply", source_id=unused_pk, kind="record.deleted"
        ).exists())

        self.composition()
        self.assertFalse(delete_or_inactivate(self.supply, "Não utilizaremos mais", self.user))
        self.supply.refresh_from_db()
        self.assertFalse(self.supply.active)

    def test_orders_page_has_compact_selector_and_single_delivery_action(self):
        order = self.order()
        self.supply.physical_stock = 10
        self.supply.save(update_fields=["physical_stock", "updated_at"])
        s.advance_demand(order.demands.get(), "ready", self.user)
        self.client.force_login(self.user)
        response = self.client.get(f"/pedidos/?selected={order.pk}")
        content = response.content.decode()
        self.assertContains(response, "Selecionar pedido")
        self.assertContains(response, "Resumo financeiro")
        self.assertContains(response, "Itens do pedido")
        self.assertEqual(content.count('data-dialog="delivery-dialog"'), 1)
        self.assertEqual(content.count(f'action="/pedidos/{order.pk}/entregar/"'), 1)
