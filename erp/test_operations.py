from datetime import timedelta
from decimal import Decimal
import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from . import tests as baseline
from . import models as m
from . import services as s
from . import operations as op
from .reminders import refresh_due, snooze_reminder, finish_reminder
from .lifecycle import cancel_record, archive_record


class OperationalTests(TestCase):
    setUp = baseline.ERPFlowTests.setUp
    composition = baseline.ERPFlowTests.composition
    quote = baseline.ERPFlowTests.quote

    def order(self, multiple=False):
        quote = self.quote()
        if multiple:
            item = m.CompositionItem.objects.create(company=self.company, composition=quote.composition, name="Tampa", quantity=3)
            m.CompositionSupply.objects.create(company=self.company, item=item, supply=self.supply, quantity=2)
            quote.composition.recalculate()
        return s.quote_to_order(quote, uuid.uuid4())

    def reminder(self, due=True):
        return m.QuoteRequest.objects.create(company=self.company, code=m.Sequence.next(self.company, "SOL"), customer=self.customer,
            description="Retornar cliente", origin="whatsapp", reminder_at=timezone.now()+timedelta(minutes=-1 if due else 10)).reminder

    def test_selected_pages_and_new_routes_render(self):
        order = self.order(True)
        reminder = self.reminder()
        product = m.Product.objects.create(company=self.company, sku="X", name="Produto pronto", current_stock=10, current_price=10)
        sale = s.create_sale(self.company, self.customer, self.method, [(product, 1)], self.account, uuid.uuid4())
        self.client.force_login(self.user)
        urls = ["/", "/solicitacoes/", f"/solicitacoes/{reminder.request_id}/", "/orcamentos/", "/pedidos/", f"/pedidos/{order.pk}/?receive=1",
            f"/producao/?order={order.pk}", f"/producao/{order.demands.first().pk}/comprar-faltantes/", "/estoque/", "/materiais/", "/clientes/",
            f"/clientes/{self.customer.pk}/?tab=finance", f"/vendas/{sale.pk}/", "/financeiro/", "/busca/?q=Cliente", "/lembretes/ativos/",
            f"/registros/Order/{order.pk}/", "/registros/?kind=Customer&archived=1"]
        for url in urls:
            with self.subTest(url=url): self.assertEqual(self.client.get(url).status_code, 200)

    def test_multiple_demands_scope_consumption_and_delivery(self):
        order = self.order(True)
        self.assertEqual(order.demands.count(), 2)
        first, second = list(order.demands.order_by("pk"))
        self.supply.physical_stock = 20
        self.supply.save()
        s.advance_demand(first, "ready", self.user)
        self.supply.refresh_from_db()
        self.assertEqual(self.supply.physical_stock, 18)
        s.advance_demand(second, "printing", self.user)
        self.assertEqual(op.order_state(order)["key"], "partial")
        self.client.force_login(self.user)
        self.client.post(f"/pedidos/{order.pk}/entregar/")
        order.refresh_from_db()
        self.assertIsNone(order.delivered_at)
        s.advance_demand(second, "ready", self.user)
        s.advance_demand(second, "ready", self.user)
        self.supply.refresh_from_db()
        self.assertEqual(self.supply.physical_stock, 12)
        self.assertEqual(op.order_state(order)["key"], "delivery")
        self.client.post(f"/pedidos/{order.pk}/entregar/")
        order.refresh_from_db()
        self.assertIsNotNone(order.delivered_at)

    def test_order_status_precedence(self):
        order = self.order(True)
        demands = list(order.demands.order_by("pk"))
        for a, b, expected in [("art","printing","art"),("material","printing","material"),("queue","printing","production"),("ready","printing","partial"),("ready","ready","delivery")]:
            m.ProductionDemand.objects.filter(pk=demands[0].pk).update(stage=a)
            m.ProductionDemand.objects.filter(pk=demands[1].pk).update(stage=b)
            self.assertEqual(op.order_state(order)["key"], expected)

    def test_regression_restores_consumption_and_reserves_once(self):
        order = self.order()
        self.supply.physical_stock = 10
        self.supply.save()
        demand = order.demands.get()
        s.advance_demand(demand, "ready")
        s.advance_demand(demand, "printing")
        self.supply.refresh_from_db()
        self.assertEqual(self.supply.physical_stock, 10)
        self.assertEqual(self.supply.reserved_stock, 2)
        s.advance_demand(demand, "material")
        self.supply.refresh_from_db()
        self.assertEqual(self.supply.reserved_stock, 0)
        s.advance_demand(demand, "ready")
        self.supply.refresh_from_db()
        self.assertEqual(self.supply.physical_stock, 8)

    def test_production_sort_late_before_urgent(self):
        normal, urgent = self.order(), self.order()
        normal.deadline = timezone.localdate()-timedelta(days=1)
        normal.save()
        urgent.priority_level = "urgent"
        urgent.save()
        demands = list(m.ProductionDemand.objects.select_related("order__customer").all())
        self.assertEqual(op.sort_demands(demands)[0].order_id, normal.pk)
        self.assertEqual(op.sort_demands(demands, "priority")[0].order_id, urgent.pk)

    def test_priority_update_propagates_and_logs_actor(self):
        order = self.order(True)
        self.client.force_login(self.user)
        self.client.post(f"/pedidos/{order.pk}/atualizar/", {"priority":"urgent", "deadline":"2027-01-01"})
        order.refresh_from_db()
        self.assertEqual(order.priority_level, "urgent")
        self.assertEqual(order.demands.filter(deadline="2027-01-01", priority=True).count(), 2)
        self.assertTrue(order.activity_events.filter(user=self.user, details__has_key="priority_level").exists())

    def test_reminder_open_does_not_finish_and_snooze_reappears(self):
        reminder = self.reminder()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/lembretes/ativos/").json()["count"], 1)
        self.assertEqual(self.client.get(f"/solicitacoes/{reminder.request_id}/").status_code, 200)
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "due")
        original = reminder.original_at
        response = self.client.post(f"/lembretes/{reminder.pk}/adiar/", {"delay":"15", "version":reminder.version}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/lembretes/ativos/").json()["count"], 0)
        reminder.refresh_from_db()
        self.assertEqual(reminder.original_at, original)
        self.assertEqual(m.RequestReminder.objects.count(), 1)
        refresh_due(self.company, reminder.scheduled_at+timedelta(seconds=1))
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, "due")
        finish_reminder(reminder, self.user)
        finish_reminder(reminder, self.user)
        self.assertEqual(m.ActivityEvent.objects.filter(kind="reminder.completed").count(), 1)

    def test_stale_snooze_and_custom_date(self):
        reminder = self.reminder()
        original_version = reminder.version
        later = timezone.now()+timedelta(days=2)
        snooze_reminder(reminder, later, reminder.version, self.user)
        with self.assertRaises(ValidationError): snooze_reminder(reminder, later, original_version, self.user)
        reminder.refresh_from_db()
        self.client.force_login(self.user)
        response = self.client.post(f"/lembretes/{reminder.pk}/adiar/", {"delay":"custom", "version":reminder.version, "when":timezone.localtime(later).strftime("%Y-%m-%dT%H:%M")}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)

    def test_reminder_auto_finish_only_explicit_purpose(self):
        first = self.reminder()
        s.request_to_quote(first.request, uuid.uuid4())
        first.refresh_from_db()
        self.assertEqual(first.status, "scheduled")
        second = self.reminder()
        second.purpose = "quote"
        second.save()
        s.request_to_quote(second.request, uuid.uuid4())
        second.refresh_from_db()
        self.assertEqual(second.status, "completed")
        self.assertEqual(second.completion_mode, "quote")

    def test_reminders_scoped_and_viewer_read_only(self):
        reminder = self.reminder()
        other = get_user_model().objects.create_user("outro")
        membership = m.Membership.objects.create(user=other, company=self.company, role="operator")
        reminder.assignee = self.user
        reminder.save()
        self.client.force_login(other)
        self.assertEqual(self.client.get("/lembretes/ativos/").json()["count"], 0)
        self.assertEqual(self.client.post(f"/lembretes/{reminder.pk}/adiar/", {"delay":"5", "version":0}).status_code, 404)
        membership.role="viewer"
        membership.save()
        self.assertEqual(self.client.post(f"/solicitacoes/{reminder.request_id}/", {"action":"reminder_complete"}).status_code, 403)

    def test_shortage_purchase_multi_item_idempotent(self):
        order = self.order()
        extra = m.Supply.objects.create(company=self.company, name="Imã", physical_stock=0, unit_cost=1)
        m.CompositionSupply.objects.create(company=self.company, item=order.composition.items.get(), supply=extra, quantity=6)
        demand = order.demands.get()
        demand.stage = "material"
        demand.save()
        self.client.force_login(self.user)
        payload = {"supply":[self.supply.pk, extra.pk], "quantity":[1,6], "unit_cost":["0.5","1"], "supplier":"Fornecedor", "payment_method":self.method.pk, "account":self.account.pk,
            "installments":2, "first_due_date":"2026-10-01", "confirm":"on", "idempotency_key":str(uuid.uuid4())}
        url = f"/producao/{demand.pk}/comprar-faltantes/"
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        self.client.post(url, payload)
        purchase = m.Purchase.objects.get()
        self.assertEqual(purchase.items.count(), 2)
        self.assertEqual(m.FinancialEntry.objects.filter(source_type="erp.Purchase").count(), 2)
        self.assertEqual(self.client.get(f"/compras/{purchase.pk}/").status_code, 200)
        self.assertFalse(any(r["missing"] for r in op.shortage_rows(demand)))
        demand.refresh_from_db()
        self.assertEqual(demand.stage, "material")

    def test_tenant_navigation_and_search(self):
        other = m.Company.objects.create(name="Outra", slug="outra")
        foreign = m.Customer.objects.create(company=other, name="Segredo")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f"/clientes/{foreign.pk}/").status_code, 404)
        self.assertEqual(self.client.get("/busca/?q=Segredo&format=json").json()["groups"], [])
        self.assertNotContains(self.client.get(f"/clientes/{self.customer.pk}/?return_to=https://evil.example"), 'href="https://evil.example"')

    def test_stock_distribution_central_only_sale(self):
        product = m.Product.objects.create(company=self.company, sku="ST", name="Estoque", current_stock=1, current_price=10)
        store = m.ConsignedStore.objects.create(company=self.company, name="Loja")
        m.ConsignmentBalance.objects.create(company=self.company, store=store, product=product, quantity=5)
        self.assertEqual(op.stock_distribution(product)[0]["balance"].quantity, 5)
        with self.assertRaises(ValidationError): s.create_sale(self.company, self.customer, self.method, [(product, 2)], self.account, uuid.uuid4())

    def test_sale_cancellation_preserves_cash_until_reconciled(self):
        product = m.Product.objects.create(company=self.company, sku="V", name="Venda", current_stock=10, current_price=10)
        sale = s.create_sale(self.company, self.customer, self.method, [(product, 2)], self.account, uuid.uuid4())
        cancel_record(sale, "Cliente devolveu", self.user)
        cancel_record(sale, "Repetição", self.user)
        product.refresh_from_db()
        self.assertEqual(product.current_stock, 10)
        self.assertEqual(self.account.balance, 20)
        self.assertEqual(m.FinancialEntry.objects.filter(source_type="erp.Reversal", status="pending").count(), 1)
        self.assertEqual(self.customer.total_purchased, 0)

    def test_purchase_cancel_blocks_consumed_or_reserved(self):
        purchase = m.Purchase.objects.create(company=self.company, code="COMP", supplier="Fornecedor", payment_method=self.method, account=self.account)
        m.PurchaseItem.objects.create(company=self.company, purchase=purchase, supply=self.supply, quantity=5, unit_cost=1, total=5)
        s.complete_purchase(purchase, self.account)
        self.supply.refresh_from_db()
        self.supply.reserved_stock = 2
        self.supply.save()
        with self.assertRaises(ValidationError): cancel_record(purchase, "Teste", self.user)
        self.supply.reserved_stock = 0
        self.supply.save()
        cancel_record(purchase, "Teste", self.user)
        self.supply.refresh_from_db()
        self.assertEqual(self.supply.physical_stock, 1)
        self.assertFalse(m.FinancialEntry.objects.filter(status="pending", source_type="erp.Purchase").exists())

    def test_cancel_order_releases_reservations_and_can_archive(self):
        order = self.order()
        self.supply.physical_stock = 10
        self.supply.save()
        s.advance_demand(order.demands.get(), "printing")
        with self.assertRaises(ValidationError): archive_record(order, True, self.user)
        cancel_record(order, "Cliente desistiu", self.user)
        order.refresh_from_db()
        self.supply.refresh_from_db()
        self.assertEqual(self.supply.reserved_stock, 0)
        self.assertEqual(self.supply.physical_stock, 10)
        self.assertFalse(op.operating_demands(self.company).exists())
        archive_record(order, True, self.user)
        self.assertFalse(op.visible_records(m.Order, self.company).exists())
        self.assertEqual(op.visible_records(m.Order, self.company, True).count(), 1)

    def test_composition_edit_blocked_after_reservation(self):
        order = self.order()
        self.supply.physical_stock = 10
        self.supply.save()
        s.advance_demand(order.demands.get(), "printing")
        self.client.force_login(self.user)
        response = self.client.post(f"/composicao/{order.composition_id}/", {"action":"item", "quantity":1, "name":"Não adicionar"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(order.composition.items.count(), 1)

    def test_quote_status_records_actor_and_reaches_order_history(self):
        quote = self.quote()
        self.client.force_login(self.user)
        self.client.post(f"/orcamentos/{quote.pk}/status/", {"status":"approved"})
        quote.refresh_from_db()
        self.assertEqual(quote.status, "approved")
        order = s.quote_to_order(quote, uuid.uuid4())
        response = self.client.get(f"/pedidos/{order.pk}/")
        self.assertContains(response, "Aprovado")
        self.assertTrue(m.ActivityEvent.objects.filter(kind="quote.updated", user=self.user).exists())

    def test_backfill_preserves_legacy_priority_and_is_idempotent(self):
        from importlib import import_module
        from django.db import connection
        from django.apps import apps
        order = self.order()
        order.demands.update(priority=True)
        backfill = import_module("erp.migrations.0006_backfill_operational_history").backfill
        from types import SimpleNamespace
        editor = SimpleNamespace(connection=connection)
        backfill(apps, editor)
        count = m.ActivityEvent.objects.count()
        backfill(apps, editor)
        order.refresh_from_db()
        self.assertEqual(order.priority_level, "priority")
        self.assertEqual(m.ActivityEvent.objects.count(), count)
