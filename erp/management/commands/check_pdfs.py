"""Gera amostras locais para inspeção visual sem gravar emissões no banco."""
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test import Client

from erp.models import Quote, Customer, Order, ConsignmentShipment, ConsignmentSettlement


class Command(BaseCommand):
    help = 'Gera amostras de PDF em tmp/pdfs; as alterações de banco são revertidas.'

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(is_superuser=True).first()
        if not user:
            raise CommandError('Cadastre um administrador antes da validação.')
        generated = {}
        with transaction.atomic():
            client = Client(HTTP_HOST='127.0.0.1')
            client.force_login(user)
            quote = Quote.objects.filter(composition__calculated_at__isnull=False, manual_value__isnull=False).first()
            customer = Customer.objects.filter(orders__isnull=False).first()
            open_order = next((order for order in Order.objects.filter(active=True, cancelled_at__isnull=True).select_related('customer') if order.balance > 0), None)
            if open_order:
                customer = open_order.customer
            shipment = ConsignmentShipment.objects.filter(items__isnull=False).first()
            settlement = ConsignmentSettlement.objects.filter(status='completed').first()
            paths = {'relatorio': '/relatorios/financeiro.pdf'}
            if quote:
                paths['orcamento'] = f'/orcamentos/{quote.pk}.pdf'
            if customer:
                paths['extrato'] = f'/clientes/{customer.pk}/extrato.pdf'
            if shipment:
                paths['remessa'] = f'/consignacao/remessas/{shipment.pk}.pdf'
            if settlement:
                paths['prestacao'] = f'/consignacao/prestacao/{settlement.pk}.pdf'
            for name, path in paths.items():
                response = client.get(path)
                if response.status_code != 200 or response.get('Content-Type') != 'application/pdf':
                    raise CommandError(f'{path}: resposta inesperada {response.status_code}')
                generated[name] = b''.join(response.streaming_content)
            transaction.set_rollback(True)
        destination = Path('tmp/pdfs')
        destination.mkdir(parents=True, exist_ok=True)
        for name, content in generated.items():
            path = destination / f'qa-{name}.pdf'
            path.write_bytes(content)
            self.stdout.write(f'{path}: {len(content)} bytes')
