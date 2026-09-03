# Padronização visual — Praise 3D

Aplicada em 02/09/2026 conforme PROMPT_CORRECAO_VISUAL_PRAISE3D.md.

## Alterações

- Shell compartilhado com os mesmos 7 atalhos e 13 módulos, menu azul-escuro e drawer móvel.
- Tokens centralizados, tipografia legível, tabelas confortáveis, campos, badges, botões, modais e mensagens padronizados.
- Ícones Lucide 0.468.0 locais, com licença em static/icons/LICENSE.lucide.txt; sem dependência de CDN.
- Início com quatro indicadores; orçamento com itens e resumo comercial separados; pedidos com tabela e painel lateral.
- Parâmetros divididos em Empresa, Identidade visual, Cálculo e Cadastros, preservando um único formulário da empresa.
- Autocomplete acessível, confirmação visual, toasts de cinco segundos e lembretes persistentes sem botão de descarte.
- Cores, logotipo e identidade cadastrados nos parâmetros preservados.

## Verificação

- 60 testes Django aprovados (52 existentes e 8 de regressão visual).
- Django check e makemigrations --check --dry-run sem pendências.
- JavaScript validado com node --check.
- Navegação dos 13 módulos verificada em 1366, 768, 390 e 320 px, sem transbordamento horizontal da página.
- Conferência visual adicional em desktop de 1600 px.
- Testes de interface em banco isolado: cadastro rápido de cliente e solicitação; salvamento comercial do orçamento; inclusão/exclusão de item; cancelar/confirmar ação; alternância de abas; switches; adiamento de lembrete; drawer móvel.
- Modelos, serviços, operações, regras financeiras, cálculos e migrations não foram modificados.
- Banco real e arquivos de mídia não foram substituídos. Nenhuma migração é necessária.

## Arquivos e recuperação

Backup dos templates, estáticos e tags anteriores:
F:\DEV\JV\Praise-3D\backups\visual-20260902-224541

A atualização concentra-se em templates/, static/, erp/templatetags/ e erp/test_visual.py.
A licença dos ícones deve acompanhar os arquivos estáticos em qualquer implantação.

Ao abrir o sistema, use Ctrl+F5 caso o navegador mantenha estilos anteriores.
Em implantação com arquivos estáticos coletados, execute collectstatic e reinicie o processo Django conforme o procedimento habitual.
