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

### Correção 4 — operação, pedidos, compras e filtros

- Início com ações operacionais enriquecidas e área de Compras para mínimos e compras ainda não efetivadas.
- Compras com vários itens do mesmo tipo, cálculo bidirecional entre unitário/total, estados Rascunho/Pendente/Efetivada/Cancelada e uma movimentação por item.
- Contas a receber automáticas e idempotentes para entrada antecipada e saldo final do pedido, usando o prazo configurado em Parâmetros.
- Contas financeiras configuráveis e conta obrigatória somente na liquidação; títulos pendentes não alteram saldo.
- Reposição manual aceita qualquer inteiro positivo e pode ser retirada da fila somente antes de produzir efeitos, liberando reservas com auditoria.
- Produtos possuem “Em atividade” independente de “Ativo”; a inativação zera estoque central e consignado por movimentos rastreáveis.
- Cadastro completo de lojas consignadas, cálculo proporcional por “Qnt. na mesa”, conclusão da produção separada da entrega e tela consolidada de Pedidos.
- Filtros avançados ficam recolhidos atrás do botão de funil e atualizam os resultados sem recarregar a página completa.
- Exclusão segura: cadastros nunca usados podem ser removidos fisicamente; qualquer vínculo operacional força inativação e preservação do histórico.
- Migração incremental `0007_correction4_operations`; testes automatizados ampliados para 73 casos.

### Correção 3 — operação, Cliente 360 e lembretes

- Início: pendências clicáveis e próximas ações calculadas. Produção inclui arte, material, fila e impressão; prontos ficam separados.
- Pedido: prioridade Normal/Prioritário/Urgente, resumo de todas as produções, cronologia, links diretos e entrega somente após todas as demandas ficarem prontas.
- Novos pedidos calculados geram uma demanda por item da composição. Demandas antigas são preservadas; antes de iniciarem a impressão, recalcular permite separar os itens sem perder o código inicial. Consumo/reserva é limitado ao item da demanda. Alterações na composição exigem retornar todas as demandas a arte/material.
- Cliente 360: visão geral, pedidos, orçamentos, compras/PDV, financeiro e histórico. A lista de títulos detalha os saldos dos pedidos (não somar os dois novamente).
- Lembretes: banco de dados, painel persistente sem descarte, sincronização a cada 30 segundos enquanto o sistema está aberto, adiamento e conclusão dentro da solicitação. Não são notificações push com navegador fechado; reaparecem ao entrar. Lembretes antigos são mantidos como tarefas manuais, mesmo quando já há orçamento/pedido, pois a finalidade original não foi registrada.
- Busca global pela lupa do cabeçalho. Estoque separado em central, consignado e total; PDV só usa central.
- Compras de faltantes podem conter vários insumos; cada item pode ser corrigido no detalhe da compra. A entrada reavalia disponibilidade sem avançar a produção automaticamente.
- Gerenciar/arquivo: cancelamento, inativação e arquivamento sem exclusão de histórico. Estornos financeiros de valores já pagos ficam pendentes para conciliação. A devolução usa o valor bruto; taxas eventualmente devolvidas devem ser conciliadas separadamente. Cancelar PDV exige retorno físico dos produtos. Compras já consumidas/reservadas e remessas com prestação posterior têm cancelamento bloqueado.
- Migrações incrementais 0005/0006. A cronologia antiga importa somente a data de cadastro conhecida, identificando autor como Sistema; não inventa mudanças históricas.
- Testes: `python manage.py test erp` (52 testes neste ciclo). Validação local em SQLite; MySQL não foi executado nesta instalação. Para banco SQLite isolado pode-se definir `DB_SQLITE_PATH` sem trocar o banco principal.

Roteiro de conferência: crie uma solicitação com lembrete, abra/adie/conclua a tarefa; gere um orçamento com dois itens e converta; confira as duas demandas, teste bloqueio por insumo e compra de faltantes; finalize uma demanda e confirme que a entrega segue bloqueada; finalize a outra, registre recebimento e entrega; consulte as seis abas do cliente e a distribuição de consignados.

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
