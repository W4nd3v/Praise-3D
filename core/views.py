from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, Q
from django.shortcuts import render
from crm.models import Customer
from operations.models import QuoteRequest, Quote, Order, ProductionDemand
from inventory.models import Product, Material, StockMovement
from finance.models import FinancialEntry
from consignment.models import ConsignmentStore, Settlement

MODULES = {
 'requests': ('Solicitações','Gerencie e acompanhe suas solicitações de orçamento'), 'quotes': ('Orçamentos','Crie, edite e acompanhe seus orçamentos'),
 'orders': ('Pedidos','Acompanhe pedidos, pagamentos e entregas'), 'production': ('Produção / Pedidos','Acompanhe e gerencie a produção das demandas'),
 'catalog': ('Catálogo','Gerencie produtos e fichas técnicas'), 'stock': ('Estoque','Controle saldos exclusivamente por movimentações'),
 'materials': ('Materiais','Gerencie filamentos, insumos e compras'), 'customers': ('Clientes','Cadastro e visão financeira dos clientes'),
 'consignment': ('Consignação','Prestação de contas e produtos consignados'), 'finance': ('Financeiro','Entradas, saídas e resultados'),
 'reports': ('Relatórios','Indicadores operacionais e financeiros'), 'settings': ('Parâmetros','Preferências e regras do sistema')}

def company_qs(model, request): return model.objects.filter(company=request.company)

@login_required
def dashboard(request):
    c = request.company
    cards = [('Solicitações pendentes', company_qs(QuoteRequest,request).exclude(status='closed').count()),
      ('Orçamentos aguardando', company_qs(Quote,request).filter(status='sent').count()), ('Fazer arte', company_qs(ProductionDemand,request).filter(stage='art').count()),
      ('Aguardando impressão', company_qs(ProductionDemand,request).filter(stage='queue').count()), ('Imprimindo', company_qs(ProductionDemand,request).filter(stage='printing').count()),
      ('Entregas pendentes', company_qs(ProductionDemand,request).filter(stage='ready').count()),
      ('Estoque mínimo', company_qs(Product,request).filter(stock__lte=models.F('minimum_stock')).count() if False else sum(1 for p in company_qs(Product,request) if p.stock <= p.minimum_stock)),
      ('Pendentes de cálculo', company_qs(Order,request).filter(calculation_status='pending').count())]
    demands = company_qs(ProductionDemand,request).exclude(stage='ready').select_related('order__customer','product')[:12]
    ready = company_qs(ProductionDemand,request).filter(stage='ready').select_related('order__customer','product')[:8]
    return render(request,'core/dashboard.html',{'cards':cards,'demands':demands,'ready':ready,'active':'home'})

@login_required
def module_page(request, module):
    title, subtitle = MODULES[module]; rows=[]; headers=[]
    mapping = {
      'requests': (QuoteRequest,['code','customer','description','origin','status']), 'quotes': (Quote,['code','customer','final_price','status','valid_until']),
      'orders': (Order,['code','customer','deadline','value','financial_status']), 'production': (ProductionDemand,['code','stage','deadline','quantity']),
      'catalog': (Product,['sku','name','stock','minimum_stock','price']), 'stock': (StockMovement,['created_at','type','product','quantity','balance_after']),
      'materials': (Material,['name','type','color','closed_rolls','open_rolls']), 'customers': (Customer,['name','phone','email','city','active']),
      'consignment': (ConsignmentStore,['name','manager','phone','default_commission','active']), 'finance': (FinancialEntry,['due_date','type','description','amount','status'])}
    if module in mapping:
      model, fields=mapping[module]; headers=[f.replace('_',' ').title() for f in fields]
      rows=[[getattr(obj,f) for f in fields] for obj in company_qs(model,request).order_by('-created_at')[:30]]
    return render(request,'core/module.html',{'title':title,'subtitle':subtitle,'active':module,'headers':headers,'rows':rows,'module':module})
