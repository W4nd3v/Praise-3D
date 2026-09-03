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
function syncSearchable(root = document) {
  root?.querySelectorAll('select[data-search-input]').forEach(select => {
    const input = document.getElementById(select.dataset.searchInput);
    const list = document.getElementById(input.getAttribute('list'));
    list.replaceChildren(...[...select.options].filter(o=>o.value).map(o=>{const entry=document.createElement('option');entry.value=o.text;return entry}));
    input.value = select.value ? select.selectedOptions[0]?.text || '' : '';
    input.setCustomValidity('');
  });
}
function initSearchable(root = document) {
  root.querySelectorAll('#request-customer, #product-dialog select[name="category"], #product-dialog select[name="product_type"], select[data-searchable]').forEach(select => {
    if (select.dataset.searchInput) return;
    const id = `search-${crypto.randomUUID()}`;
    const input = document.createElement('input'), list = document.createElement('datalist');
    input.id=id; list.id=`${id}-options`; input.setAttribute('list',list.id);
    input.placeholder='Digite para pesquisar e selecione'; input.autocomplete='off'; input.required=select.required;
    select.required=false; select.hidden=true; select.dataset.searchInput=id;
    select.after(input,list);
    input.addEventListener('input',()=>{
      const option=[...select.options].find(o=>o.value && o.text===input.value);
      select.value=option?.value || '';
      input.setCustomValidity(input.value && !option ? 'Selecione uma opção cadastrada na lista.' : '');
      select.dispatchEvent(new Event('change',{bubbles:true}));
    });
  });
  syncSearchable(root);
}
function showFormError(form, message) {
  let error=form.querySelector('.form-error');
  if (!error) {error=document.createElement('p');error.className='form-error';form.append(error)}
  error.textContent=message; error.hidden=false;
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
      dialog?.querySelector('form')?.reset();
      if(button.dataset.dialog==='product-dialog')dialog.querySelector('[data-product-title]').textContent='Novo produto';
    }
    syncSearchable(dialog); dialog?.showModal();
  }));
  root.querySelectorAll('[data-close-dialog]').forEach(button=>bindOnce(button,'closeBound','click',()=>button.closest('dialog')?.close()));
  root.querySelectorAll('[data-tab]').forEach(button=>bindOnce(button,'tabBound','click',()=>{
    const group=button.closest('[data-tabs]');
    group?.querySelectorAll('[data-tab]').forEach(item=>item.classList.remove('active'));
    group?.querySelectorAll('[data-panel]').forEach(item=>item.hidden=true);
    button.classList.add('active'); const panel=group?.querySelector(`[data-panel="${button.dataset.tab}"]`);if(panel)panel.hidden=false;
  }));
  root.querySelectorAll('[data-confirm]').forEach(form=>bindOnce(form,'confirmBound','submit',event=>{if(!window.confirm(form.dataset.confirm))event.preventDefault()}));
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
    button.disabled=true;
    try {
      const url=button.dataset.openComposition,response=await fetch(url),parsed=new DOMParser().parseFromString(await response.text(),'text/html');
      const editor=parsed.getElementById('composition-editor');if(!editor)throw new Error('Não foi possível abrir os itens.');
      document.querySelector('[data-composition-host]').replaceChildren(editor);
      initCompositionEditors(editor,url);initUi(editor);document.getElementById('quote-composition-dialog').showModal();
      if(button.hasAttribute('data-add-item'))document.getElementById('item-dialog').showModal();
      if(button.dataset.itemId)editor.querySelector(`.edit-item[data-id="${button.dataset.itemId}"]`)?.click();
    } catch(error){alert(error.message)} finally {button.disabled=false}
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
    if(form.dataset.submitting){event.preventDefault();return}form.dataset.submitting='1';
    setTimeout(()=>form.querySelectorAll('button[type=submit],button:not([type])').forEach(button=>button.disabled=true),0);
  }));
}
function initCompositionEditors(root = document, actionUrl=null) {
  if(actionUrl)root.querySelectorAll('[data-composition-form]').forEach(form=>form.setAttribute('action',actionUrl));
  const bind=(selector,dialog,values)=>root.querySelectorAll(selector).forEach(button=>bindOnce(button,'fillBound','click',()=>fillNamedForm(document.querySelector(`${dialog} form`),values(button.dataset))));
  bind('.add-part','#part-dialog',d=>({item:d.item,part_id:'',name:'',grams:'',print_minutes:'',quantity:'1'}));
  bind('.edit-part','#part-dialog',d=>({part_id:d.id,item:d.item,name:d.name,family:d.family,grams:d.grams,print_minutes:d.minutes,printer:d.printer,quantity:d.quantity}));
  bind('.add-supply','#supply-dialog',d=>({item:d.item,use_id:'',quantity:'1'}));
  bind('.edit-use','#supply-dialog',d=>({use_id:d.id,item:d.item,supply:d.supply,quantity:d.quantity}));
  bind('.edit-item','#edit-item-dialog',d=>({item:d.id,name:d.name,description:d.description,quantity:d.quantity,unit:d.unit}));
}
function initMessages(root=document) {
  root.querySelectorAll('.message button').forEach(button=>bindOnce(button,'messageBound','click',()=>button.parentElement.remove()));
  root.querySelectorAll('.message:not(.persistent)').forEach(message=>{if(!message.dataset.timeoutBound){message.dataset.timeoutBound='1';setTimeout(()=>message.remove(),5000)}});
}
document.addEventListener('DOMContentLoaded',()=>{
  initCompositionEditors(document);initUi(document);initMessages(document);updateQuotePreview();
  document.querySelector('[data-toggle-sidebar]')?.addEventListener('click',()=>document.querySelector('.sidebar')?.classList.toggle('open'));
});
