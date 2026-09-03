# Praise 3D ERP

ERP web multiempresa para operação de empresas de impressão 3D, desenvolvido com Python, Django e MySQL.

## Módulos

- Central de operação e alertas
- Clientes, solicitações, orçamentos e pedidos
- Compositor único de custos para orçamento, pedido e catálogo
- Produção com as etapas Fazer arte, Aguardando material, Aguardando impressão, Imprimindo e Pronto
- Catálogo, estoque por movimentações e reposição
- Filamentos por família + cor + rolos fechados/em uso
- Insumos, compras e baixa automática na conclusão
- PDV, pagamentos, fluxo financeiro e parcelamento
- Consignação por remessa e prestação por contagem
- Parâmetros multiempresa, snapshots e relatórios PDF

## Preparação

No PowerShell, a partir desta pasta:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edite `.env` com o usuário e a senha do MySQL. Crie o banco e um usuário dedicado no MySQL:

```sql
CREATE DATABASE praise3d CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'praise3d'@'localhost' IDENTIFIED BY 'SENHA_FORTE';
GRANT ALL PRIVILEGES ON praise3d.* TO 'praise3d'@'localhost';
FLUSH PRIVILEGES;
```

Depois:

```powershell
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Abra `http://127.0.0.1:8000/`.

Dados demonstrativos: usuário `admin`, senha `Praise3D@2026`. Troque a senha antes de qualquer uso real.

## Regras de material preservadas

- Composição e produção trabalham com família de material, nunca com cor.
- Cor existe apenas no estoque de filamentos.
- Filamento não é reservado nem baixado em gramas por pedido.
- A fila operacional começa em `Aguardando material`; `Fazer arte` continua visível nos pedidos e no painel.
- Filamento não é reservado e nunca bloqueia o avanço da produção.
- Insumos extras são reservados antes de `Aguardando impressão`; saldo insuficiente bloqueia o avanço.
- A reserva dos insumos é baixada quando a demanda chega a `Pronto`.

## Testes

Os testes usam SQLite isoladamente, sem tocar no MySQL configurado:

```powershell
$env:DB_ENGINE='sqlite'
python manage.py test
```

## Atualização de correções — setembro/2026

As alterações são incrementais. Não execute `seed_demo` novamente na base que já contém seus cadastros.

- Solicitações com busca de cliente e cadastro rápido sem sair da tela.
- Itens do orçamento em modal, prévia automática e valor comercial manual preservado.
- Motor compartilhado: base de cálculo, custo direto e base da margem independentes; manutenção por hora e margem/desperdício opcionais por composição.
- Produção com etapas clicáveis, reservas de insumos e falhas calculadas pelo snapshot da parte.
- Edição de cadastros, categorias/tipos, SKU sequencial e ajustes auditáveis de estoque.
- Correções de compra geram diferenças rastreáveis; parcelas pagas não são reescritas e ajustes financeiros ficam pendentes de confirmação.
- Remessas em página completa, prestação com retorno ao estoque e opção de iniciar nova remessa.
- PDFs com identidade dos Parâmetros; versões emitidas ficam acessíveis em **Relatórios → Documentos emitidos**. A preservação em arquivo começa com este mecanismo; PDFs emitidos antes dele não podem ser recuperados automaticamente.

O valor sugerido segue `base de cálculo + base da margem × percentual`. Impostos e desconto técnico legados permanecem no valor técnico ajustado; não substituem o valor manual enviado ao cliente. Vencimentos no extrato vêm dos títulos financeiros, nunca da data de entrega do pedido.

Validação local:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test erp
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
# Opcional: gera amostras locais em tmp/pdfs, revertendo as emissões no banco.
.\.venv\Scripts\python.exe manage.py check_pdfs
```

Neste ambiente de testes, `.env` permanece com `DB_ENGINE=sqlite`; não foi feita troca ou importação para MySQL. O servidor existente usa `http://127.0.0.1:8001/`. Antes das alterações foi salvo o backup `backups/db-antes-correcoes-2026-09-02.sqlite3`. As migrações novas são aditivas e preservam os registros existentes.
