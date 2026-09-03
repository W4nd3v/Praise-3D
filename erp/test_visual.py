"""Regression coverage for the visual layer without changing business behavior."""
import re
from html.parser import HTMLParser
from pathlib import Path
from django.conf import settings
from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase
from . import tests as baseline
from .templatetags.ui import icon
from .templatetags.erp_tags import stock_label, status_class


class FormFields(HTMLParser):
    def __init__(self, target):
        super().__init__()
        self.target = target
        self.current = None
        self.fields = []
        self.forms = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = attrs.get("id")
            if self.current == self.target:
                self.forms += 1
        if self.current == self.target and tag in {"input", "select", "textarea"}:
            if attrs.get("name"):
                self.fields.append(attrs["name"])

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class VisualTemplateTests(SimpleTestCase):
    def test_every_template_compiles(self):
        for path in (settings.BASE_DIR / "templates").rglob("*.html"):
            with self.subTest(path=path.name):
                get_template(path.relative_to(settings.BASE_DIR / "templates").as_posix())

    def test_no_native_message_dialogs(self):
        files = list((settings.BASE_DIR / "templates").rglob("*.html"))
        files += list((settings.BASE_DIR / "static/js").rglob("*.js"))
        for path in files:
            self.assertIsNone(re.search(r"\b(?:alert|confirm|prompt)\s*\(", path.read_text(encoding="utf-8")), path.name)

    def test_semantic_stock_and_stage_labels(self):
        self.assertEqual(stock_label('warning'), 'Atenção')
        self.assertEqual(stock_label('critical'), 'Crítico')
        self.assertEqual(status_class('art'), 'art')
        self.assertEqual(status_class('material'), 'warning')
        self.assertEqual(status_class('ready'), 'success')

    def test_icons_are_local_decorative_and_whitelisted(self):
        rendered = str(icon("house"))
        self.assertIn("/static/icons/lucide.svg#house", rendered)
        self.assertIn('aria-hidden="true"', rendered)
        self.assertIn("#circle-help", str(icon('"><script>')))
        self.assertNotIn("<script>", str(icon('"><script>')))


class VisualPageTests(TestCase):
    setUp = baseline.ERPFlowTests.setUp
    composition = baseline.ERPFlowTests.composition
    quote = baseline.ERPFlowTests.quote

    def page(self, url):
        self.client.force_login(self.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_shell_and_only_four_dashboard_kpis(self):
        html = self.page("/")
        navigation = html.split('<nav class="quick-actions"')[1].split("</nav>")[0]
        sidebar = html.split('<nav class="side-nav"')[1].split("</nav>")[0]
        metrics = html.split("data-dashboard-kpis")[1].split("</section>")[0]
        self.assertEqual(navigation.count("<a "), 7)
        self.assertEqual(sidebar.count("<a "), 13)
        self.assertEqual(metrics.count('<article class="metric '), 4)
        for title in ["Solicitações pendentes", "Orçamentos aguardando", "Em fazer arte", "Cálculos pendentes"]:
            self.assertIn(title, metrics)

    def test_parameters_sections_preserve_all_company_fields_in_one_form(self):
        html = self.page("/parametros/")
        parser = FormFields("company-form")
        parser.feed(html)
        expected = {
            "action", "name", "trading_name", "responsible_name", "document", "state_registration",
            "slogan", "phone", "whatsapp", "instagram", "email", "website", "address", "state",
            "postal_code", "city", "logo", "primary_color", "secondary_color", "success_color",
            "warning_color", "energy_rate", "labor_hour_rate", "fixed_cost_per_order",
            "waste_percent", "default_margin_percent", "default_filament_minimum",
            "pricing_method", "material_cost_policy", "csrfmiddlewaretoken",
        }
        self.assertEqual(parser.forms, 1)
        self.assertEqual(set(parser.fields), expected)
        self.assertEqual(len(parser.fields), len(expected))
        for section in ["company", "brand", "calculation", "registrations"]:
            self.assertIn('data-settings-tab="' + section + '"', html)

    def test_quote_summary_and_commercial_form_remain_connected(self):
        quote = self.quote()
        html = self.page(f"/orcamentos/?selected={quote.pk}")
        parser = FormFields("quote-commercial")
        parser.feed(html)
        self.assertEqual(set(parser.fields), {"csrfmiddlewaretoken", "manual_value", "freight_amount", "valid_until", "payment_terms", "notes"})
        for attribute in ["quote-detail", "quote-items", "quote-summary", "quote-main", "quote-aside",
                          "data-quote-final", "data-quote-profit", "data-quote-margin", "data-save-quote-first", "data-convert-quote"]:
            self.assertIn(attribute, html)

    def test_other_modules_render_with_shared_shell(self):
        for url in ["/solicitacoes/", "/pedidos/", "/producao/", "/catalogo/", "/estoque/",
                    "/materiais/", "/clientes/", "/consignacao/", "/financeiro/", "/relatorios/",
                    "/busca/?q=Cliente", "/registros/?kind=Customer"]:
            with self.subTest(url=url):
                html = self.page(url)
                self.assertIn('aria-label="Menu principal"', html)
                self.assertIn('id="confirm-dialog"', html)
                self.assertIn('css/app.css', html)
