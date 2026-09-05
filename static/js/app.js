const currency = new Intl.NumberFormat('pt-BR', {style:'currency', currency:'BRL'});
const decimalInput = value => Number(String(value || 0).includes(',') ? String(value).replaceAll('.', '').replace(',', '.') : value || 0);
function bindOnce(element, key, event, handler) {
  if (!element || element.dataset[key]) return;
  element.dataset[key] = '1'; element.addEventListener(event, handler);
}
function fillNamedForm(form, values) {
  Object.entries(values).forEach(([name, value]) => {
    const field = form?.querySelector(`[name="${name}"]`);
    if (field) field.value = field.type === 'number' && value !== '' && value != null ? String(decimalInput(value)) : value ?? '';
  });
  syncSearchable(form);
}
function uiIcon(name) {
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),use=document.createElementNS('http://www.w3.org/2000/svg','use');
  svg.setAttribute('class','icon');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('fill','none');
  svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.75');svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');svg.setAttribute('aria-hidden','true');
  use.setAttribute('href','/static/icons/lucide.svg#'+name);svg.append(use);return svg;
}
function showToast(message, type='error') {
  let host=document.querySelector('.messages');
  if(!host){host=document.createElement('div');host.className='messages';host.setAttribute('aria-live','polite');document.body.append(host)}
  const box=document.createElement('div'),text=document.createElement('span'),close=document.createElement('button');
  box.className='message '+type;text.textContent=message;close.type='button';close.setAttribute('aria-label','Fechar');
  close.append(uiIcon('x'));box.append(uiIcon(type==='success'?'circle-check':'info'),text,close);host.append(box);initMessages(host);
}
function askConfirmation(message) {
  const dialog=document.getElementById('confirm-dialog');
  if(dialog.open)return Promise.resolve(false);
  document.getElementById('confirm-description').textContent=message;
  dialog.returnValue='';
  return new Promise(resolve=>{
    const accept=()=>dialog.close('confirmed'),cancel=()=>dialog.close('cancelled');
    const yes=dialog.querySelector('[data-confirm-accept]'),no=dialog.querySelector('[data-confirm-cancel]');
    yes.addEventListener('click',accept);no.addEventListener('click',cancel);
    dialog.addEventListener('close',()=>{yes.removeEventListener('click',accept);no.removeEventListener('click',cancel);resolve(dialog.returnValue==='confirmed')},{once:true});
    dialog.showModal();no.focus();
  });
}
function enhanceAutocomplete(input, getOptions) {
  if(input.dataset.comboboxBound)return;
  input.dataset.comboboxBound='1';
  const wrap=document.createElement('div'),list=document.createElement('ul'),id=input.id || 'lookup-'+crypto.randomUUID();
  input.id=id;wrap.className='combobox';list.className='combobox-list';list.id=id+'-list';list.hidden=true;list.setAttribute('role','listbox');
  input.before(wrap);wrap.append(input,uiIcon('chevron-down'),list);
  input.removeAttribute('list');input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-expanded','false');input.setAttribute('aria-controls',list.id);input.autocomplete='off';
  if(!input.getAttribute('aria-label'))input.setAttribute('aria-label',input.closest('label')?.childNodes[0]?.textContent.trim() || input.placeholder || 'Pesquisar');
  let options=[],active=-1;
  const normalize=value=>value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('pt-BR');
  function close(){list.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');active=-1}
  function highlight(index){active=index;[...list.children].forEach((li,i)=>li.setAttribute('aria-selected',String(i===index)));const selected=list.children[index];if(selected){input.setAttribute('aria-activedescendant',selected.id);selected.scrollIntoView({block:'nearest'})}}
  function choose(index){const option=options[index];if(!option)return;input.value=option.text;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));close();input.focus()}
  function render(showAll=false){
    options=getOptions().filter(o=>!o.disabled && (showAll || normalize(o.text).includes(normalize(input.value)))).slice(0,80);list.replaceChildren();active=-1;
    options.forEach((option,index)=>{const li=document.createElement('li');li.textContent=option.text;li.id=list.id+'-'+index;li.setAttribute('role','option');li.setAttribute('aria-selected','false');li.addEventListener('mousedown',event=>{event.preventDefault();choose(index)});list.append(li)});
    if(!options.length){const li=document.createElement('li');li.className='no-results';li.textContent='Nenhum resultado. Confira o nome informado.';list.append(li)}
    list.hidden=false;input.setAttribute('aria-expanded','true');
  }
  input.addEventListener('input',()=>render());
  input.addEventListener('focus',()=>render(true));
  input.addEventListener('click',()=>{if(list.hidden)render(true)});
  input.addEventListener('blur',()=>close());
  input.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&!list.hidden){event.preventDefault();event.stopPropagation();close()}
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();if(list.hidden)render(true);if(options.length)highlight((active+(event.key==='ArrowDown'?1:-1)+options.length)%options.length)}
    if(event.key==='Enter'&&!list.hidden&&active>=0){event.preventDefault();choose(active)}
  });
}
function syncSearchable(root = document) {
  root?.querySelectorAll('select[data-search-input]').forEach(select=>{
    const input=document.getElementById(select.dataset.searchInput);
    if(input){input.value=select.value?select.selectedOptions[0]?.text||'':'';input.setCustomValidity('')}
  });
}
function initSearchable(root = document) {
  root.querySelectorAll('#request-customer, #product-dialog select[name="category"], #product-dialog select[name="product_type"], select[data-searchable]').forEach(select=>{
    if(select.dataset.searchInput)return;
    const input=document.createElement('input');input.id='search-'+crypto.randomUUID();input.placeholder='Pesquisar e selecionar';input.required=select.required;
    select.required=false;select.hidden=true;select.dataset.searchInput=input.id;select.after(input);
    input.addEventListener('input',()=>{
      const option=[...select.options].find(o=>o.value&&o.text===input.value);
      select.value=option?.value||'';input.setCustomValidity(input.value&&!option?'Selecione uma opção cadastrada na lista.':'');
      select.dispatchEvent(new Event('change',{bubbles:true}));
    });
    enhanceAutocomplete(input,()=>[...select.options].filter(o=>o.value).map(o=>({text:o.text,disabled:o.disabled})));
  });
  root.querySelectorAll('input[list]').forEach(input=>{
    const list=document.getElementById(input.getAttribute('list'));
    if(list)enhanceAutocomplete(input,()=>[...list.options].map(o=>({text:o.value,disabled:o.disabled})));
  });
  syncSearchable(root);
}
function showFormError(form, message) {
  let error=form.querySelector('.form-error');
  if (!error) {error=document.createElement('p');error.className='form-error';form.append(error)}
  error.textContent=message; error.hidden=false;
}
function runPageScripts(parsed) {
  parsed.querySelectorAll('script:not([src])').forEach(script=>{
    if(script.type && script.type!=='text/javascript')return;
    try{new Function(`(()=>{${script.textContent}\n})()`)()}catch(error){console.error('Falha ao reinicializar a tela',error)}
  });
}
async function replacePageContent(url, push=true) {
  const current=document.getElementById('page-content');
  if(!current){window.location.href=url;return}
  current.classList.add('is-loading');
  try{
    const response=await fetch(url,{headers:{'X-Requested-With':'XMLHttpRequest'}});
    if(!response.ok)throw new Error('Não foi possível atualizar os resultados.');
    const parsed=new DOMParser().parseFromString(await response.text(),'text/html'),incoming=parsed.getElementById('page-content');
    if(!incoming)throw new Error('A tela recebida é inválida.');
    current.replaceWith(incoming);document.title=parsed.title;
    if(push)history.pushState({praiseAjax:true},'',url);
    runPageScripts(parsed);initCompositionEditors(incoming);initUi(incoming);initMessages(incoming);initFilters(incoming);
  }catch(error){current.classList.remove('is-loading');showToast(error.message)}
}
function initFilters(root=document) {
  const params=new URLSearchParams(location.search);
  root.querySelectorAll('form[data-filters]').forEach(form=>{
    form.querySelectorAll('[name]').forEach(field=>{if(params.has(field.name))field.value=params.get(field.name)});
    if(form.dataset.filtersBound)return;
    form.dataset.filtersBound='1';form.classList.add('filters-enhanced');
    const direct=[...form.children],searchHolder=direct.find(node=>node.matches?.('input[name="q"],input[type="search"]')||node.querySelector?.('input[name="q"],input[type="search"]'));
    const searchRow=document.createElement('div'),panel=document.createElement('div'),toggle=document.createElement('button'),close=document.createElement('button');
    searchRow.className='filter-search-row';panel.className='filter-panel';panel.hidden=true;toggle.type='button';toggle.className='button filter-toggle';toggle.append(uiIcon('funnel'),document.createTextNode(' Filtros'));
    const hidden=direct.filter(node=>node.matches?.('input[type="hidden"]'));
    direct.forEach(node=>{if(node!==searchHolder&&!hidden.includes(node))panel.append(node)});
    if(searchHolder)searchRow.append(searchHolder);
    const searchButton=document.createElement('button');searchButton.className='button primary';searchButton.type='submit';searchButton.append(uiIcon('search'),document.createTextNode(' Buscar'));
    if(searchHolder)searchRow.append(searchButton);
    searchRow.append(toggle);close.type='button';close.className='button ghost';close.textContent='Fechar';panel.append(close);form.append(searchRow,panel);
    const fieldNames=[...panel.querySelectorAll('[name]')].filter(field=>field.type!=='hidden').map(field=>field.name);
    const active=[...new Set(fieldNames.filter(name=>params.has(name)&&params.get(name)!==''))].length;
    if(active){const badge=document.createElement('b');badge.className='filter-count';badge.textContent=active;toggle.append(badge);toggle.classList.add('active')}
    toggle.addEventListener('click',()=>{panel.hidden=!panel.hidden;toggle.setAttribute('aria-expanded',String(!panel.hidden));if(!panel.hidden)panel.querySelector('select,input')?.focus()});
    close.addEventListener('click',()=>{panel.hidden=true;toggle.setAttribute('aria-expanded','false');toggle.focus()});
    form.addEventListener('submit',event=>{event.preventDefault();const target=new URL(form.action||location.href,location.href),data=new FormData(form);target.search='';for(const [name,value] of data){if(String(value).trim())target.searchParams.append(name,String(value).trim())}replacePageContent(target.toString())});
  });
}
async function replaceOrderSelection(url,push=true) {
  const detail=document.getElementById('order-detail');
  if(!detail){window.location.href=url;return}
  detail.classList.add('is-loading');
  try{
    const response=await fetch(url,{headers:{'X-Requested-With':'XMLHttpRequest'}}),parsed=new DOMParser().parseFromString(await response.text(),'text/html');
    const incoming=parsed.getElementById('order-detail'),incomingSelector=parsed.querySelector('[data-order-selector]');
    if(!response.ok||!incoming)throw new Error('Não foi possível carregar o pedido.');
    detail.replaceWith(incoming);const selector=document.querySelector('[data-order-selector]');if(selector&&incomingSelector)selector.replaceWith(incomingSelector);
    document.title=parsed.title;if(push)history.pushState({praiseOrder:true},'',url);
    initUi(incoming);initUi(incomingSelector);initMessages(incoming);
    incoming.scrollIntoView({behavior:'smooth',block:'start'});
  }catch(error){detail.classList.remove('is-loading');showToast(error.message)}
}
async function refreshQuote() {
  const detail=document.getElementById('quote-detail');
  if (!detail) return;
  const response=await fetch(detail.dataset.quoteUrl);
  if(!response.ok) throw new Error('Não foi possível atualizar o orçamento.');
  const parsed=new DOMParser().parseFromString(await response.text(),'text/html');
  for(const id of ['quote-items','quote-summary']) {
    const incoming=parsed.getElementById(id), current=document.getElementById(id);
    if(incoming && current) current.replaceWith(incoming);
  }
  initUi(document.getElementById('quote-items')); updateQuotePreview();
}
function updateQuotePreview() {
  const form=document.getElementById('quote-commercial'), summary=document.getElementById('quote-summary');
  if(!form || !summary) return;
  const input=form.querySelector('[name="manual_value"]'), value=decimalInput(input.value);
  const base=Number(summary.dataset.base), marginBase=Number(summary.dataset.marginBase), profit=value-base;
  document.querySelector('[data-quote-final]').textContent=input.value ? currency.format(value) : 'Pendente';
  const profitNode=document.querySelector('[data-quote-profit]');
  profitNode.textContent=input.value ? currency.format(profit) : '—';
  profitNode.className=profit<0?'text-danger':'text-success';
  document.querySelector('[data-quote-margin]').textContent=input.value && marginBase>0 ? `${(profit/marginBase*100).toLocaleString('pt-BR',{maximumFractionDigits:3})}%` : 'Não aplicável';
  document.querySelector('[data-quote-loss]').hidden=!input.value || profit>=0;
}
let quoteSavePromise;
async function saveQuote() {
  const form=document.getElementById('quote-commercial');
  if(!form || !form.reportValidity()) return false;
  if(quoteSavePromise) return quoteSavePromise;
  quoteSavePromise=(async()=>{
    try {
      const response=await fetch(form.getAttribute('action'),{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'XMLHttpRequest'}});
      const data=await response.json();
      if(!response.ok) throw new Error(data.error || 'Não foi possível salvar.');
      form.querySelector('.form-error').hidden=true;
      document.querySelector('[data-commercial-status]').textContent='Orçamento salvo';
      showToast('Orçamento salvo com sucesso.','success');
      return true;
    } catch(error) {showFormError(form,error.message);return false}
    finally {quoteSavePromise=null}
  })();
  return quoteSavePromise;
}
const totalLabels={'Filamento':'material_cost','Energia':'energy_cost','Manutenção':'maintenance_cost','Depreciação':'depreciation_cost','Mão de obra':'labor_cost','Insumos':'supplies_cost','Base de cálculo':'base_calculation','Custo direto':'direct_cost','Base da margem':'margin_base','Valor da margem':'margin_value','Preço sugerido':'suggested_price','Lucro técnico':'predicted_profit'};
function updateCompositionTotals(totals, preview=false) {
  const editor=document.getElementById('composition-editor');
  editor?.querySelectorAll('.summary-cell').forEach(cell=>{
    const key=totalLabels[cell.querySelector('small')?.textContent.trim()];
    if(key && totals[key]!==undefined) cell.querySelector('strong').textContent=currency.format(Number(totals[key]));
  });
  const state=editor?.querySelector('.card-head .status');
  if(state) state.textContent=preview ? 'Prévia · salve o item para confirmar' : 'Cálculo salvo';
  editor?.querySelectorAll('dialog[open] .dialog-body').forEach(body=>{
    let box=body.querySelector('.live-preview');
    if(!box){box=document.createElement('div');box.className='note live-preview';body.append(box)}
    box.textContent=`Prévia do orçamento: base ${currency.format(Number(totals.base_calculation))} · sugerido ${currency.format(Number(totals.suggested_price))}`;
  });
}
function initUi(root = document) {
  if(!root) return;
  initSearchable(root);
  root.querySelectorAll('[data-dialog]').forEach(button=>bindOnce(button,'dialogBound','click',()=>{
    const dialog=document.getElementById(button.dataset.dialog);
    if(['product-dialog','filament-dialog','supply-dialog','store-dialog','customer-dialog','printer-dialog','family-dialog','payment-dialog','category-dialog','type-dialog'].includes(button.dataset.dialog) && !button.matches('[class*="edit-"],[class*="add-"],[class*="adjust-"]')) {
      if(dialog?.dataset.editorMode==='edit')dialog.querySelector('form')?.reset();
      if(dialog)dialog.dataset.editorMode='create';
      if(button.dataset.dialog==='product-dialog')dialog.querySelector('[data-product-title]').textContent='Novo produto';
    }
    if(button.matches('[class*="edit-"]') && dialog)dialog.dataset.editorMode='edit';
    syncSearchable(dialog); dialog?.showModal();
  }));
  root.querySelectorAll('[data-close-dialog]').forEach(button=>bindOnce(button,'closeBound','click',()=>button.closest('dialog')?.close()));
  root.querySelectorAll('[data-tab]').forEach(button=>bindOnce(button,'tabBound','click',()=>{
    const group=button.closest('[data-tabs]');
    group?.querySelectorAll('[data-tab]').forEach(item=>item.classList.remove('active'));
    group?.querySelectorAll('[data-panel]').forEach(item=>item.hidden=true);
    button.classList.add('active'); const panel=group?.querySelector(`[data-panel="${button.dataset.tab}"]`);if(panel)panel.hidden=false;
  }));
  root.querySelectorAll('[data-confirm]').forEach(form=>bindOnce(form,'confirmBound','submit',async event=>{
    if(form.dataset.confirmApproved){delete form.dataset.confirmApproved;return}
    event.preventDefault();const submitter=event.submitter;
    if(await askConfirmation(form.dataset.confirm)){form.dataset.confirmApproved='1';if(submitter)form.requestSubmit(submitter);else form.requestSubmit()}
  }));
  root.querySelectorAll('.clickable-row[data-href]').forEach(row=>bindOnce(row,'rowBound','click',event=>{if(!event.target.closest('a,button,input,select,form'))window.location.href=row.dataset.href}));
  const reminder=root.querySelector('[name=reminder_enabled]'), reminderFields=root.querySelector('[data-reminder-fields]');
  if(reminder && reminderFields)bindOnce(reminder,'reminderBound','change',()=>reminderFields.hidden=!reminder.checked);
  root.querySelectorAll('form[data-quick-customer]').forEach(form=>bindOnce(form,'quickBound','submit',async event=>{
    event.preventDefault();if(form.dataset.busy)return;form.dataset.busy='1';
    try {
      const response=await fetch(form.getAttribute('action'),{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'XMLHttpRequest'}}),data=await response.json();
      if(!response.ok || !data.ok)throw new Error(data.error || 'Não foi possível cadastrar.');
      const select=document.getElementById('request-customer');select.add(new Option(data.name,String(data.id),true,true));syncSearchable(select.parentElement);
      form.closest('dialog').close();form.reset();
    } catch(error){showFormError(form,error.message)} finally {delete form.dataset.busy}
  }));
  root.querySelectorAll('[data-open-composition]').forEach(button=>bindOnce(button,'composerBound','click',async()=>{
    button.disabled=true;button.classList.add('is-loading');
    try {
      const url=button.dataset.openComposition,response=await fetch(url),parsed=new DOMParser().parseFromString(await response.text(),'text/html');
      const editor=parsed.getElementById('composition-editor');if(!editor)throw new Error('Não foi possível abrir os itens.');
      document.querySelector('[data-composition-host]').replaceChildren(editor);
      initCompositionEditors(editor,url);initUi(editor);document.getElementById('quote-composition-dialog').showModal();
      if(button.hasAttribute('data-add-item'))document.getElementById('item-dialog').showModal();
      if(button.dataset.itemId)editor.querySelector(`.edit-item[data-id="${button.dataset.itemId}"]`)?.click();
    } catch(error){showToast(error.message)} finally {button.disabled=false;button.classList.remove('is-loading')}
  }));
  root.querySelectorAll('.edit-store').forEach(button=>bindOnce(button,'storeEditBound','click',()=>{
    fillNamedForm(document.querySelector('#store-dialog form'),{store_id:button.dataset.id,name:button.dataset.name,contact_name:button.dataset.contact,phone:button.dataset.phone,address:button.dataset.address,commission:button.dataset.commission,notes:button.dataset.notes});
  }));
  root.querySelectorAll('.correct-purchase').forEach(button=>bindOnce(button,'purchaseDraftBound','click',()=>{
    const form=document.querySelector('#purchase-correct-dialog form'),draft=form.querySelector('[data-purchase-draft]'),completed=button.dataset.completed==='1';
    draft.hidden=completed;draft.querySelectorAll('input,select').forEach(field=>field.disabled=completed);
    fillNamedForm(form,{supplier:button.dataset.supplier,purchase_date:button.dataset.date,payment_method:button.dataset.method,installments:button.dataset.installments,first_due_date:button.dataset.due});
  }));
  root.querySelectorAll('[data-alert-adjust]').forEach(button=>bindOnce(button,'alertAdjustBound','click',()=>document.querySelector(`#${button.dataset.alertAdjust} [class*="adjust-"]`)?.click()));
  root.querySelectorAll('[data-order-select]').forEach(link=>bindOnce(link,'orderSelectBound','click',event=>{event.preventDefault();replaceOrderSelection(link.href)}));
  root.querySelectorAll('form[data-order-schedule-form]').forEach(form=>bindOnce(form,'orderScheduleBound','submit',async event=>{
    event.preventDefault();if(form.dataset.busy)return;form.dataset.busy='1';
    try{const response=await fetch(form.action,{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'XMLHttpRequest'}}),data=await response.json();if(!response.ok||!data.ok)throw new Error(data.error||'Não foi possível atualizar o pedido.');form.closest('dialog')?.close();showToast('Prioridade e prazo atualizados.','success');await replaceOrderSelection(location.href,false)}catch(error){showFormError(form,error.message)}finally{delete form.dataset.busy}
  }));
  root.querySelectorAll('form[data-delivery-form], form[data-quick-delivery]').forEach(form=>bindOnce(form,'deliveryBound','submit',async event=>{
    event.preventDefault();if(form.dataset.busy)return;if(!await askConfirmation('Confirma a entrega deste pedido?'))return;form.dataset.busy='1';
    try{const response=await fetch(form.action,{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'XMLHttpRequest'}}),data=await response.json();if(!response.ok||!data.ok)throw new Error(data.error||'Não foi possível registrar a entrega.');form.closest('dialog')?.close();showToast('Entrega registrada no pedido e no histórico do cliente.','success');if(document.getElementById('order-detail'))await replaceOrderSelection(location.href,false);else form.closest('[data-auto-open]')?.close()}catch(error){showFormError(form,error.message)}finally{delete form.dataset.busy}
  }));
  root.querySelectorAll('#payment-dialog form').forEach(form=>bindOnce(form,'paymentAccountBound','submit',event=>{if(form.elements.received_now?.checked&&!form.elements.account?.value){event.preventDefault();showFormError(form,'Selecione a conta de destino para um recebimento liquidado.');form.elements.account.focus()}}));
  root.querySelectorAll('.adjust-supply').forEach(button=>bindOnce(button,'supplyDifference','click',()=>{
    const form=document.querySelector('#supply-adjust-dialog form'),input=form.querySelector('[name=physical_stock]');
    let note=form.querySelector('[data-stock-difference]');if(!note){note=document.createElement('p');note.dataset.stockDifference='1';input.parentElement.after(note)}
    const before=decimalInput(button.dataset.stock);const update=()=>note.textContent=`Estoque atual: ${before.toLocaleString('pt-BR')} · Diferença: ${(decimalInput(input.value)-before).toLocaleString('pt-BR')}`;
    input.oninput=update;update();
  }));
  root.querySelectorAll('form[data-composition-form]').forEach(form=>{
    bindOnce(form,'compositionBound','submit',async event=>{
      if(event.defaultPrevented)return;event.preventDefault();
      if(form.dataset.busy){form.dataset.queued='1';return}form.dataset.busy='1';
      const url=form.getAttribute('action') || window.location.href;
      try {
        const response=await fetch(url,{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'XMLHttpRequest'}});
        if(response.headers.get('Content-Type')?.includes('application/json')) {
          const data=await response.json();if(!response.ok)throw new Error(data.error || 'Falha ao salvar.');
          updateCompositionTotals(data.totals);await refreshQuote();
        } else {
          const parsed=new DOMParser().parseFromString(await response.text(),'text/html'), incoming=parsed.getElementById('composition-editor');
          if(!response.ok || !incoming)throw new Error('Não foi possível salvar a composição.');
          document.getElementById('composition-editor')?.replaceWith(incoming);initCompositionEditors(incoming,url);initUi(incoming);await refreshQuote();
        }
      } catch(error){showFormError(form,error.message)} finally {delete form.dataset.busy;if(form.dataset.queued && form.isConnected){delete form.dataset.queued;form.requestSubmit()}}
    });
    let timer,revision=0;
    form.querySelectorAll('input:not([type=hidden]),select,textarea').forEach(field=>{
      const schedule=()=>{clearTimeout(timer);const expected=++revision;timer=setTimeout(async()=>{
        if(!form.checkValidity())return;
        if(form.hasAttribute('data-auto-submit')){form.requestSubmit();return}
        const body=new FormData(form);body.set('preview','1');
        try {const response=await fetch(form.getAttribute('action') || window.location.href,{method:'POST',body,headers:{'X-Requested-With':'XMLHttpRequest'}}),data=await response.json();if(response.ok && expected===revision)updateCompositionTotals(data.totals,true)}catch(error){showFormError(form,'Não foi possível calcular a prévia. Tente novamente.')}
      },400)};
      bindOnce(field,'previewInput','input',schedule);bindOnce(field,'previewChange','change',schedule);
    });
  });
  root.querySelectorAll('#quote-commercial').forEach(form=>{
    bindOnce(form,'commercialInput','input',()=>{updateQuotePreview();document.querySelector('[data-commercial-status]').textContent='Alterações não salvas'});
    bindOnce(form,'commercialSave','submit',event=>{event.preventDefault();saveQuote()});
  });
  root.querySelectorAll('[data-save-quote-first]').forEach(link=>bindOnce(link,'savePdf','click',async event=>{event.preventDefault();const target=window.open('about:blank','_blank');if(await saveQuote()){if(target)target.location.href=link.href;else window.location.href=link.href}else target?.close()}));
  root.querySelectorAll('[data-convert-quote]').forEach(form=>bindOnce(form,'saveConvert','submit',async event=>{event.preventDefault();if(await saveQuote())HTMLFormElement.prototype.submit.call(form)}));
  root.querySelectorAll('form').forEach(form=>bindOnce(form,'doubleSubmit','submit',event=>{
    if(event.defaultPrevented)return;
    if(form.dataset.submitting){event.preventDefault();return}
    queueMicrotask(()=>{if(event.defaultPrevented)return;form.dataset.submitting='1';form.setAttribute('aria-busy','true');form.querySelectorAll('button[type=submit],button:not([type])').forEach(button=>button.disabled=true)});
  }));
}
function initCompositionEditors(root = document, actionUrl=null) {
  if(actionUrl)root.querySelectorAll('[data-composition-form]').forEach(form=>form.setAttribute('action',actionUrl));
  const bind=(selector,dialog,values)=>root.querySelectorAll(selector).forEach(button=>bindOnce(button,'fillBound','click',()=>fillNamedForm(document.querySelector(`${dialog} form`),values(button.dataset))));
  bind('.add-part','#part-dialog',d=>({item:d.item,part_id:'',name:'',plate_quantity:'1',grams:'',print_minutes:'',quantity:'1'}));
  bind('.edit-part','#part-dialog',d=>({part_id:d.id,item:d.item,name:d.name,family:d.family,plate_quantity:d.plate||'1',grams:d.grams,print_minutes:d.minutes,printer:d.printer,quantity:d.quantity}));
  bind('.add-supply','#supply-dialog',d=>({item:d.item,use_id:'',quantity:'1'}));
  bind('.edit-use','#supply-dialog',d=>({use_id:d.id,item:d.item,supply:d.supply,quantity:d.quantity}));
  bind('.edit-item','#edit-item-dialog',d=>({item:d.id,name:d.name,description:d.description,quantity:d.quantity,unit:d.unit}));
}
function initMessages(root=document) {
  root.querySelectorAll('.message button').forEach(button=>bindOnce(button,'messageBound','click',()=>button.parentElement.remove()));
  root.querySelectorAll('.message:not(.persistent)').forEach(message=>{if(!message.dataset.timeoutBound){message.dataset.timeoutBound='1';setTimeout(()=>message.remove(),5000)}});
}
document.addEventListener('DOMContentLoaded',()=>{
  initCompositionEditors(document);initUi(document);initMessages(document);initFilters(document);updateQuotePreview();
  const menu=document.querySelector('[data-toggle-sidebar]'),sidebar=document.querySelector('.sidebar'),backdrop=document.querySelector('[data-close-sidebar]');
  function setMenu(open){sidebar?.classList.toggle('open',open);document.body.classList.toggle('sidebar-open',open);menu?.setAttribute('aria-expanded',String(open));if(backdrop)backdrop.hidden=!open;if(sidebar)sidebar.inert=matchMedia('(max-width:760px)').matches&&!open;if(open)sidebar?.querySelector('a')?.focus();else menu?.focus()}
  menu?.addEventListener('click',()=>setMenu(!sidebar.classList.contains('open')));
  backdrop?.addEventListener('click',()=>setMenu(false));
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&sidebar?.classList.contains('open'))setMenu(false)});
  const mobileMedia=matchMedia('(max-width:760px)');
  if(sidebar)sidebar.inert=mobileMedia.matches;
  mobileMedia.addEventListener('change',()=>{if(sidebar?.classList.contains('open'))setMenu(false);if(sidebar)sidebar.inert=mobileMedia.matches});
  document.addEventListener('keydown',event=>{
    if(event.key!=='Tab'||!sidebar?.classList.contains('open'))return;
    const links=[...sidebar.querySelectorAll('a,button')],first=links[0],last=links.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();menu.focus()}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();menu.focus()}
    else if(document.activeElement===menu){event.preventDefault();(event.shiftKey?last:first)?.focus()}
  });
  document.querySelectorAll('dialog').forEach(dialog=>{
    if(!dialog.hasAttribute('aria-labelledby')){const title=dialog.querySelector('.dialog-head h2');if(title){title.id=title.id||dialog.id+'-title';dialog.setAttribute('aria-labelledby',title.id)}}
  });
  document.querySelectorAll('.side-nav a.active').forEach(link=>link.setAttribute('aria-current','page'));
  document.querySelectorAll('.switch-line input').forEach(input=>{
    input.setAttribute('role','switch');
    if(input.closest('.compact')){const row=input.closest('tr'),index=[...row.children].indexOf(input.closest('td')),header=input.closest('table').querySelectorAll('th')[index];input.setAttribute('aria-label',(row.children[0]?.textContent.trim()||'Componente')+' · '+(header?.textContent.trim()||'Ativar'))}
  });
  const settingsTabs=[...document.querySelectorAll('[data-settings-tab]')];
  function showSettings(key){if(!settingsTabs.some(b=>b.dataset.settingsTab===key))key='company';settingsTabs.forEach(button=>{const active=button.dataset.settingsTab===key;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active))});document.querySelectorAll('[data-settings-section]').forEach(section=>section.hidden=section.dataset.settingsSection!==key);try{sessionStorage.setItem('praise-settings-section',key)}catch{}}
  if(settingsTabs.length){let active='company';try{active=sessionStorage.getItem('praise-settings-section')||active}catch{}showSettings(active);settingsTabs.forEach(button=>button.addEventListener('click',()=>showSettings(button.dataset.settingsTab)));document.getElementById('company-form')?.addEventListener('invalid',event=>{const section=event.target.closest('[data-settings-section]');if(section)showSettings(section.dataset.settingsSection)},true)}
  document.querySelectorAll('[data-tabs]').forEach((group,index)=>{
    group.querySelector('.tabs')?.setAttribute('role','tablist');
    const buttons=[...group.querySelectorAll('[data-tab]')];
    buttons.forEach((button,number)=>{
      const panel=group.querySelector('[data-panel="'+button.dataset.tab+'"]');if(!panel)return;
      button.id=button.id||'tab-'+index+'-'+number;panel.id=panel.id||'panel-'+index+'-'+number;
      button.setAttribute('role','tab');button.setAttribute('aria-controls',panel.id);panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',button.id);
      const refresh=()=>buttons.forEach(b=>{const on=b.classList.contains('active');b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1});
      refresh();button.addEventListener('click',refresh);
      button.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const target=event.key==='Home'?0:event.key==='End'?buttons.length-1:(number+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[target].click();buttons[target].focus()}});
    });
  });
  const params=new URLSearchParams(location.search);
  document.querySelectorAll('form[data-filters] [name]').forEach(field=>{if(params.has(field.name))field.value=params.get(field.name)});
  document.querySelectorAll('[data-tabs][data-initial-tab]').forEach(group=>[...group.querySelectorAll('[data-tab]')].find(b=>b.dataset.tab===group.dataset.initialTab)?.click());
  document.querySelectorAll('dialog[data-auto-open="1"], dialog[data-auto-open="True"]').forEach(dialog=>dialog.showModal());
  const host=document.getElementById('reminder-host'), dialog=document.getElementById('reminder-snooze-dialog'), form=dialog?.querySelector('form');
  let polling=false;
  async function refreshReminders(force=false){
    if(!host || polling || (!force && (dialog?.open || host.matches(':focus-within'))))return;
    polling=true;
    try{const response=await fetch(host.dataset.feed,{headers:{'Accept':'application/json'}});if(!response.ok)return;const data=await response.json();
      const wasExpanded=host.querySelector('.expanded'),wasMinimized=host.querySelector('.minimized');host.innerHTML=data.html;const panel=host.querySelector('.reminder-persistent');if(wasExpanded){const button=panel?.querySelector('[data-expand-reminders]');panel?.classList.add('expanded');if(button){button.setAttribute('aria-expanded','true');button.textContent='Mostrar menos'}}if(wasMinimized){const button=panel?.querySelector('[data-minimize-reminders]');panel?.classList.add('minimized');if(button){button.setAttribute('aria-expanded','false');button.textContent='Abrir'}}
      document.querySelectorAll('[data-reminder-count]').forEach(b=>{b.textContent=data.count;b.hidden=!data.count});
      document.querySelectorAll('[data-reminder-total]').forEach(b=>b.textContent=data.count);
      document.querySelectorAll('[data-reminder-task]').forEach(a=>a.hidden=!data.request_ids.includes(Number(a.dataset.reminderTask)));
    }catch(error){/* Keep the last visible reminders during a network failure. */}finally{polling=false}
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-snooze-id]');
    if(button){form.action=`/lembretes/${button.dataset.snoozeId}/adiar/`;form.elements.version.value=button.dataset.version;form.querySelector('.form-error').hidden=true;dialog.showModal()}
    const expandButton=event.target.closest('[data-expand-reminders]');
    if(expandButton){const panel=host.querySelector('.reminder-persistent'),expanded=panel?.classList.toggle('expanded');expandButton.setAttribute('aria-expanded',String(Boolean(expanded)));expandButton.textContent=expanded?'Mostrar menos':'Ver todos'}
    const minimizeButton=event.target.closest('[data-minimize-reminders]');
    if(minimizeButton){const panel=host.querySelector('.reminder-persistent'),minimized=panel?.classList.toggle('minimized');minimizeButton.setAttribute('aria-expanded',String(!minimized));minimizeButton.textContent=minimized?'Abrir':'Recolher'}
  });
  form?.elements.delay.addEventListener('change',()=>{const custom=form.elements.delay.value==='custom';form.querySelector('[data-custom-reminder]').hidden=!custom;form.elements.when.required=custom});
  // Capture before the generic double-submit handler: failures must remain retryable.
  form?.addEventListener('submit',async event=>{event.preventDefault();if(form.dataset.busy)return;form.dataset.busy='1';
    try{const response=await fetch(form.action,{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'XMLHttpRequest'}});const data=await response.json();if(!response.ok)throw new Error(data.error || 'Não foi possível adiar.');dialog.close();await refreshReminders(true)}catch(error){showFormError(form,error.message)}finally{delete form.dataset.busy}
  },true);
  setInterval(()=>refreshReminders(),30000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshReminders()});
});
window.addEventListener('popstate',()=>replacePageContent(location.href,false));
