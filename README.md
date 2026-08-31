# Praise3D ERP

ERP multiempresa para impressão 3D, construído com Django e MySQL. A base inclui CRM, solicitações, orçamentos, pedidos, produção, catálogo/estoque, materiais por rolo, financeiro, consignação, auditoria e snapshots históricos.

## Primeira execução

1. Crie no MySQL o banco e usuário em UTF-8 (`utf8mb4`).
2. Copie `.env.example` para `.env` e defina as credenciais no ambiente (o Django lê variáveis do sistema).
3. Ative a venv e execute:

```powershell
.\.venv\Scripts\Activate.ps1
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

No primeiro acesso, cadastre uma Empresa e uma Associação (Membership) para o seu usuário em `/admin/`.

## Teste local sem MySQL

```powershell
$env:DB_ENGINE='sqlite'
python manage.py migrate
python manage.py test
python manage.py runserver
```

SQLite existe somente como apoio ao desenvolvimento/teste; produção usa MySQL.

## Arquitetura

- `core`: empresa, vínculo de usuários, sequências, auditoria e idempotência.
- `crm`: clientes isolados por empresa.
- `operations`: solicitação, orçamento, pedido e demanda produtiva.
- `inventory`: catálogo, estoque por movimentação, filamentos, insumos e modelos de cálculo.
- `finance`: contas, formas, títulos, vendas e estornos.
- `consignment`: lojas, remessas e prestação por contagem.

As operações compostas ficam em `services.py` e utilizam transações e chaves idempotentes.
