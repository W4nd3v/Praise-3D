from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (
    CalculationModel, Company, Composition, CompositionItem, CompositionSupply,
    ConsignedStore, Customer, Filament, FinancialAccount, FinancialEntry,
    ManufacturingPart, MaterialFamily, Membership, PaymentMethod, Printer,
    Product, ProductCategory, ProductType, Supply,
)


class CadastroIdTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Teste de cadastros", slug="teste-cadastros")
        self.user = get_user_model().objects.create_user("cadastros")
        Membership.objects.create(company=self.company, user=self.user, role="admin")
        self.client.force_login(self.user)
        self.family = MaterialFamily.objects.create(company=self.company, name="PLA", reference_cost_kg=80)
        self.calculation = CalculationModel.objects.create(company=self.company, name="Padrão", default=True)
        self.supply = Supply.objects.create(company=self.company, name="Insumo base", unit_cost=1)

    def create_cases(self):
        return [
            (Customer, "/clientes/", "customer_id", {"name": "Cliente novo"}),
            (Filament, "/materiais/", "filament_id", {"action": "filament", "family": self.family.pk, "color": "Azul", "cost": "80"}),
            (Supply, "/materiais/", "supply_id", {"action": "supply", "name": "Insumo novo", "cost": "2"}),
            (Printer, "/parametros/", "printer_id", {"action": "printer", "name": "Impressora nova", "cost": "1000"}),
            (MaterialFamily, "/parametros/", "family_id", {"action": "family", "name": "Material novo", "cost": "60"}),
            (PaymentMethod, "/parametros/", "payment_id", {"action": "payment", "name": "Pagamento novo", "kind": "pix"}),
            (CalculationModel, "/parametros/", "calculation_id", {"action": "calculation", "name": "Modelo novo", "margin": "40", "active": "on"}),
            (ProductCategory, "/parametros/", "item_id", {"action": "product_category", "name": "Categoria nova"}),
            (ProductType, "/parametros/", "item_id", {"action": "product_type", "name": "Tipo novo"}),
            (ConsignedStore, "/consignacao/", "store_id", {"action": "store", "name": "Loja nova", "commission": "15"}),
            (Product, "/catalogo/", "product_id", {"name": "Produto novo", "category": "", "product_type": ""}),
        ]

    def assert_creations(self, identifier):
        for model, url, field, payload in self.create_cases():
            with self.subTest(model=model.__name__, identifier=identifier):
                data = dict(payload)
                if identifier is not None:
                    data[field] = identifier
                before = model.objects.filter(company=self.company).count()
                response = self.client.post(url, data)
                self.assertEqual(model.objects.filter(company=self.company).count(), before + 1)
                self.assertEqual(response.status_code, 302)

    def test_create_with_empty_hidden_id(self):
        self.assert_creations("")

    def test_create_without_hidden_id(self):
        self.assert_creations(None)

    def test_create_with_whitespace_hidden_id(self):
        self.assert_creations("  ")

    def test_invalid_identifier_does_not_create_or_leak_database_error(self):
        for model, url, field, payload in self.create_cases():
            for bad_id in [".", "abc", "1.5", "0", "-1", "9" * 40]:
                with self.subTest(model=model.__name__, bad_id=bad_id):
                    before = model.objects.filter(company=self.company).count()
                    response = self.client.post(url, {**payload, field: bad_id}, follow=True)
                    self.assertEqual(model.objects.filter(company=self.company).count(), before)
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, "Identificador inválido")
                    self.assertNotContains(response, "expected a number")

    def test_edit_existing_ids_updates_without_duplicating(self):
        for model, url, field, payload in self.create_cases():
            with self.subTest(model=model.__name__):
                ids = set(model.objects.values_list("pk", flat=True))
                self.client.post(url, {**payload, field: ""})
                instance = model.objects.exclude(pk__in=ids).get()
                before = model.objects.count()
                change = "color" if model is Filament else "name"
                self.client.post(url, {**payload, field: str(instance.pk), change: "Revisado"})
                instance.refresh_from_db()
                self.assertEqual(getattr(instance, change), "Revisado")
                self.assertEqual(model.objects.count(), before)

    def test_composition_creates_part_without_printer_and_supply_with_empty_id(self):
        composition = Composition.objects.create(company=self.company, name="Composição", calculation_model=self.calculation)
        item = CompositionItem.objects.create(company=self.company, composition=composition, name="Item", quantity=1)
        url = f"/composicao/{composition.pk}/"
        response = self.client.post(url, {"action": "part", "part_id": "", "item": item.pk,
            "family": self.family.pk, "printer": "", "name": "Parte nova", "quantity": "1", "grams": "50", "print_minutes": "30"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ManufacturingPart.objects.filter(item=item).count(), 1)
        self.assertIsNone(item.parts.get().printer_id)
        response = self.client.post(url, {"action": "supply", "use_id": "", "item": item.pk, "supply": self.supply.pk, "quantity": "2"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CompositionSupply.objects.filter(item=item).count(), 1)

    def test_finance_allows_empty_optional_customer_and_payment(self):
        account = FinancialAccount.objects.create(company=self.company, name="Caixa")
        response = self.client.post("/financeiro/", {"direction": "out", "account": account.pk,
            "customer": "", "payment_method": "", "description": "Despesa", "category": "Operação", "amount": "10"})
        self.assertEqual(response.status_code, 302)
        entry = FinancialEntry.objects.get(company=self.company)
        self.assertEqual(entry.gross_amount, Decimal("10"))
        self.assertIsNone(entry.customer_id)
        self.assertIsNone(entry.payment_method_id)

    def test_invalid_model_id_does_not_clear_current_default(self):
        self.client.post("/parametros/", {"action": "calculation", "calculation_id": ".", "name": "Inválido", "default": "on"})
        self.calculation.refresh_from_db()
        self.assertTrue(self.calculation.default)

    def test_foreign_company_edit_id_cannot_create_or_update_customer(self):
        other = Company.objects.create(name="Outra empresa", slug="outra-empresa")
        customer = Customer.objects.create(company=other, name="Preservar")
        before = Customer.objects.count()
        response = self.client.post("/clientes/", {"customer_id": customer.pk, "name": "Alterado"})
        customer.refresh_from_db()
        self.assertEqual(customer.name, "Preservar")
        self.assertEqual(Customer.objects.count(), before)
        self.assertContains(response, "Cadastro não encontrado")
