'use strict';
const $ = id => document.getElementById(id);
let snapshot = {state: 'STATE_GREETING', order: null, messages: []}, menu = [], category = 'All', busy = false, aiAvailable = false;
let pending = null, aiCheckSequence = 0, lastUXReport = null;
try { pending = JSON.parse(sessionStorage.getItem('bistro_pending') || 'null'); } catch (_) {}
function node(tag, text, cls) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; }
const money = cents => new Intl.NumberFormat('en-PH', {style: 'currency', currency: 'PHP'}).format(cents / 100);
function notify(message, error = false) { $('notice').textContent = message; $('notice').className = error ? 'error' : ''; $('notice').hidden = !message; }
function editable() { return !!snapshot.order && !['STATE_CONFIRMED', 'STATE_CANCELLED'].includes(snapshot.state); }
async function get(path, options = {}) { const response = await fetch('/api/' + path, {cache: 'no-store', ...options}); const data = await response.json(); if (!response.ok) throw new Error(data.message || 'Could not load restaurant data.'); return data; }
function savePending(value) { pending = value; try { if (value) sessionStorage.setItem('bistro_pending', JSON.stringify(value)); else sessionStorage.removeItem('bistro_pending'); } catch (_) {} $('retry-box').hidden = !value; }
function setBusy(value) { busy = value; updateControls(); if (!value) reportUX(); }
function updateControls() {
  $('message').disabled = busy || !editable() || !aiAvailable;
  $('send').disabled = $('message').disabled;
  $('review-order').disabled = busy || !editable() || !snapshot.order.items.length;
  $('cancel-order').disabled = busy || !editable();
  $('confirm-order').disabled = busy || !snapshot.review_token;
  $('edit-order').disabled = busy || snapshot.state !== 'STATE_REVIEW';
  $('table-form').querySelector('button').disabled = busy || editable();
  $('table-no').disabled = busy || editable();
  // AI availability only gates chat. Recompute ordering from its own current state.
  document.querySelectorAll('#menu-items form').forEach(form => {
    const disabled = busy || !editable() || form.dataset.available !== 'true';
    form.querySelectorAll('input, button').forEach(el => { el.disabled = disabled; });
  });
  document.querySelectorAll('#cart-items input, #cart-items button').forEach(el => { el.disabled = busy || !editable(); });
  $('new-session').disabled = busy;
  $('start-new-order').disabled = busy;
  $('retry-request').disabled = busy;
}
async function refresh() {
  const values = await Promise.all([get('session'), get('menu')]); snapshot = values[0]; menu = values[1]; render();
}
async function sendRequest(path, body, replay = null) {
  if (busy) return;
  if (pending && !replay) { notify('Resolve the interrupted request with Retry same request before sending another action.', true); return; }
  const request = replay || {path, body, id: crypto.randomUUID()}; savePending(request); setBusy(true);
  $('thinking').hidden = request.path !== 'chat';
  try {
    const response = await fetch('/api/' + request.path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Bistro-Request': request.id}, body: JSON.stringify(request.body)});
    const result = await response.json();
    if (result.code !== 'request_pending') savePending(null);
    if (result.snapshot) snapshot = result.snapshot;
    notify((result.code && !result.ok ? result.code.replaceAll('_', ' ') + ': ' : '') + result.message, !result.ok);
    if (result.ok && snapshot.state === 'STATE_CANCELLED') notify('Order cancelled successfully. Start a new order, choose another table, or return to the menu.');
    if (request.path === 'chat' && result.ok) $('message').value = '';
    // Never render a cached old snapshot as the current cart after a retry.
    await refresh();
    if (request.path === 'chat' && !result.ok) {
      if (['ai_unavailable', 'ollama_unavailable', 'model_missing', 'ai_disabled'].includes(result.code)) showAI(false, result.message);
      checkAI(); // Never hold ordering controls for an AI availability probe.
    }
    if (snapshot.review || snapshot.state === 'STATE_CONFIRMED') selectPanel('cart-panel');
  } catch (_) {
    notify('Connection interrupted. Your request may already be saved. Refresh the cart or use Retry same request; it will not repeat saved changes.', true);
    render();
  } finally { $('thinking').hidden = true; setBusy(false); }
}
function action(name, args = {}) { return sendRequest('action', {action: name, arguments: args}); }
function selectPanel(id) { document.querySelectorAll('.panel').forEach(el => el.classList.toggle('active-panel', el.id === id)); document.querySelectorAll('[data-panel]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.panel === id))); }
function renderMenu() {
  $('categories').replaceChildren();
  const categories = ['All', ...new Set(menu.map(item => item.category))];
  if (!categories.includes(category)) category = 'All';
  categories.forEach(value => { const button = node('button', value); button.setAttribute('aria-pressed', String(category === value)); button.onclick = () => { category = value; renderMenu(); }; $('categories').append(button); });
  $('menu-items').replaceChildren();
  menu.filter(item => category === 'All' || item.category === category).forEach(item => {
    const card = node('article', undefined, 'menu-card');
    card.append(node('p', item.category, 'category'), node('h3', item.name), node('p', money(item.price_cents), 'price'));
    if (item.description) card.append(node('p', item.description, 'details'));
    const available = !!item.available && item.stock_qty > 0;
    card.append(node('p', available ? item.stock_qty + ' available' : 'Currently unavailable', 'stock' + (available ? '' : ' unavailable')));
    card.append(node('p', 'Allergens: ' + (item.allergens.join(', ') || 'None listed'), 'details'));
    const form = node('form'), label = node('label', 'Special instructions'), input = node('input');
    form.dataset.available = String(available);
    form.hidden = ['STATE_CANCELLED', 'STATE_CONFIRMED'].includes(snapshot.state);
    input.id = 'note-' + item.id; label.htmlFor = input.id; input.maxLength = 500; input.placeholder = 'Optional'; input.disabled = busy || !editable() || !available;
    const row = node('div', undefined, 'input-row'), qty = node('input'), button = node('button', '+ Add');
    qty.type = 'number'; qty.min = '1'; qty.max = String(Math.min(10000, item.stock_qty)); qty.step = '1'; qty.value = '1'; qty.required = true; qty.setAttribute('aria-label', 'Quantity of ' + item.name); qty.disabled = input.disabled;
    button.type = 'submit'; button.disabled = input.disabled; button.setAttribute('aria-label', 'Add ' + item.name + ' to order');
    row.append(qty, button); form.append(label, input, row);
    form.onsubmit = event => { event.preventDefault(); sendRequest('action', {action: 'add_item', arguments: {item_name: item.name, qty: Number(qty.value), instructions: input.value}}); };
    card.append(form); $('menu-items').append(card);
  });
  if (!$('menu-items').children.length) $('menu-items').append(node('p', 'No menu items in this category.', 'empty'));
}
function totals(order, target) {
  const box = node('div', undefined, 'totals');
  [['Subtotal', 'subtotal_cents'], ['Tax', 'tax_cents'], ['Service', 'service_cents'], ['Total', 'total_cents']].forEach(([label, key]) => { const row = node('div', undefined, key === 'total_cents' ? 'grand-total' : ''); row.append(node('span', label), node('span', money(order[key]))); box.append(row); });
  target.replaceChildren(box);
}
function line(item, canEdit) {
  const box = node('div', undefined, 'cart-line'); box.append(node('h3', item.name));
  const summary = node('div', undefined, 'line-summary'); summary.append(node('span', item.quantity + ' × ' + money(item.unit_price_cents)), node('strong', money(item.line_total_cents))); box.append(summary);
  if (!canEdit) { box.append(node('p', item.special_instructions || 'No special instructions', 'muted')); return box; }
  const quantityForm = node('form'), row = node('div', undefined, 'input-row'), qty = node('input'), save = node('button', 'Update'), remove = node('button', 'Remove', 'text-button');
  qty.type = 'number'; qty.min = '1'; qty.max = '10000'; qty.step = '1'; qty.required = true; qty.value = item.quantity; qty.setAttribute('aria-label', 'Quantity of ' + item.name); qty.disabled = busy;
  save.type = 'submit'; save.disabled = busy; remove.type = 'button'; remove.disabled = busy; remove.onclick = () => action('remove_item', {line_id: item.line_id}); row.append(qty, save, remove); quantityForm.append(row);
  quantityForm.onsubmit = event => { event.preventDefault(); action('update_quantity', {line_id: item.line_id, qty: Number(qty.value)}); };
  const noteForm = node('form'), label = node('label', 'Special instructions'), input = node('input'), noteSave = node('button', 'Save note', 'text-button');
  input.id = 'line-note-' + item.line_id; label.htmlFor = input.id; input.value = item.special_instructions; input.maxLength = 500; input.disabled = busy; noteSave.type = 'submit'; noteSave.disabled = busy; noteForm.append(label, input, noteSave);
  noteForm.onsubmit = event => { event.preventDefault(); action('update_instructions', {line_id: item.line_id, instructions: input.value}); }; box.append(quantityForm, noteForm); return box;
}
function render() {
  const order = snapshot.order, closed = ['STATE_CONFIRMED', 'STATE_CANCELLED'].includes(snapshot.state);
  $('table-label').textContent = order ? 'Table ' + order.table_no : 'Welcome, ka-bistro';
  $('table-form').hidden = !!order && !closed; $('new-session').hidden = !closed;
  $('recovery').hidden = !closed;
  document.querySelector('.order-actions').hidden = closed;
  $('chat-form').hidden = closed;
  $('cart-count').textContent = order ? order.items.reduce((count, item) => count + item.quantity, 0) : 0;
  $('order-state').textContent = !order ? 'Start a table session to order.' : snapshot.state === 'STATE_CANCELLED' ? 'Order cancelled successfully. No stock was deducted and no receipt was created. Choose a recovery action below.' : snapshot.state === 'STATE_CONFIRMED' ? 'Confirmed · Your receipt is saved.' : snapshot.state === 'STATE_REVIEW' ? 'Awaiting your explicit confirmation.' : 'Table ' + order.table_no + ' · Changes are saved automatically.';
  $('cart-items').replaceChildren(); $('totals').replaceChildren();
  if (order && order.items.length && snapshot.state !== 'STATE_CONFIRMED') { order.items.forEach(item => $('cart-items').append(line(item, editable()))); totals(order, $('totals')); }
  else if (snapshot.state !== 'STATE_CONFIRMED') $('cart-items').append(node('p', 'Your cart is waiting for something delicious.', 'empty'));
  $('review').hidden = !(snapshot.state === 'STATE_REVIEW' && snapshot.review && snapshot.review_token);
  if (!$('review').hidden) { $('review-items').replaceChildren(...snapshot.review.items.map(item => line(item, false))); totals(snapshot.review, $('review-total')); }
  $('receipt').hidden = !(order && order.receipt_id);
  if (!$('receipt').hidden) { $('receipt-meta').textContent = 'Table ' + order.table_no + ' · Receipt ' + order.receipt_id + ' · ' + order.created_at; $('receipt-items').replaceChildren(...order.items.map(item => line(item, false))); totals(order, $('receipt-total')); }
  $('messages').replaceChildren();
  if (!snapshot.messages.length) { const welcome = node('div', undefined, 'chat-welcome'); welcome.append(node('span', '✦'), node('h3', 'Kumusta, ka-bistro!'), node('p', 'Ask about the menu or order in English, Filipino, or Taglish.')); $('messages').append(welcome); }
  snapshot.messages.forEach(message => { const box = node('div', undefined, 'message ' + message.role); box.append(node('span', message.role === 'customer' ? 'YOU' : 'BISTRO WAITER', 'speaker'), node('span', message.text)); $('messages').append(box); });
  $('messages').scrollTop = $('messages').scrollHeight;
  renderMenu(); updateControls(); if (!busy) reportUX();
}
function reportUX() {
  if (busy) return;
  const terminal = ['STATE_CANCELLED', 'STATE_CONFIRMED'].includes(snapshot.state);
  const visible = el => !el.hidden && !el.closest('[hidden]');
  const newOrder = visible($('start-new-order')) && !$('start-new-order').disabled;
  const menuAvailable = !$('refresh-menu').disabled && Array.from(document.querySelectorAll('[data-panel]')).every(el => !el.disabled);
  const stocked = menu.some(item => (category === 'All' || item.category === category) && item.available && item.stock_qty > 0);
  const addEnabled = Array.from(document.querySelectorAll('#menu-items button')).some(el => !el.disabled);
  const unexpected = (editable() && stocked && !addEnabled) || (terminal && (!menuAvailable || $('return-menu').disabled));
  const statusCorrect = snapshot.state !== 'STATE_CANCELLED' || $('order-state').textContent.includes('Order cancelled successfully.');
  const report = {event: snapshot.state === 'STATE_CANCELLED' ? 'cancel_order_workflow' : 'ux_state',
    new_order_available: newOrder, menu_available: menuAvailable,
    ordering_available: editable() && (addEnabled || !!document.querySelector('#cart-items button:not(:disabled)')),
    controls_disabled_unexpectedly: unexpected, status_correct: statusCorrect, ai_available: aiAvailable};
  const encoded = JSON.stringify(report);
  if (encoded === lastUXReport) return;
  lastUXReport = encoded;
  // Boolean observations only. Logging must never lock controls or affect an order.
  fetch('/api/acceptance-ux', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Bistro-Request': crypto.randomUUID()}, body: encoded})
    .then(response => { if (!response.ok) lastUXReport = null; }).catch(() => { lastUXReport = null; });
}
function showAI(available, message, model = '') {
  aiAvailable = available === true;
  $('ai-status').textContent = (aiAvailable ? 'AI available: ' : 'AI unavailable: ') + message + (model ? ' · ' + model : '') +
    (aiAvailable ? '' : ' Use the menu and cart controls to order without AI.');
  $('ai-status').parentElement.classList.toggle('unavailable', !aiAvailable);
  updateControls(); if (!busy) reportUX();
}
async function checkAI() {
  const sequence = ++aiCheckSequence;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const status = await get('ai-status', {signal: controller.signal});
    if (sequence === aiCheckSequence) showAI(status.available, status.message + ' (' + status.endpoint + ')', status.model);
  } catch (_) {
    if (sequence === aiCheckSequence) showAI(false, 'Could not check local Ollama.');
  } finally { clearTimeout(timeout); }
}
$('table-form').onsubmit = event => { event.preventDefault(); sendRequest('session', {table_no: Number($('table-no').value)}); };
$('start-new-order').onclick = () => sendRequest('session', {table_no: snapshot.order.table_no});
$('return-menu').onclick = () => { selectPanel('menu-panel'); $('menu-title').scrollIntoView({block: 'start'}); };
$('new-session').onclick = () => { $('table-form').hidden = false; $('new-session').hidden = true; $('table-no').value = ''; $('table-no').focus(); };
$('chat-form').onsubmit = event => { event.preventDefault(); if (!$('message').disabled && $('message').value.trim()) sendRequest('chat', {message: $('message').value}); };
$('message').onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('chat-form').requestSubmit(); } };
$('review-order').onclick = () => action('request_review');
$('edit-order').onclick = () => action('return_to_order');
$('confirm-order').onclick = () => sendRequest('confirm', {confirmed: true, review_token: snapshot.review_token});
$('cancel-order').onclick = () => { if (window.confirm('Cancel this order? No checkout will be performed.')) action('cancel_order'); };
$('retry-request').onclick = () => { if (pending) sendRequest(pending.path, pending.body, pending); };
$('check-ai').onclick = checkAI;
$('print-receipt').onclick = () => window.print();
for (const id of ['refresh-menu', 'refresh-cart']) $(id).onclick = () => refresh().catch(error => notify(error.message, true));
document.querySelectorAll('[data-panel]').forEach(button => button.onclick = () => selectPanel(button.dataset.panel));
$('retry-box').hidden = !pending;
Promise.all([refresh(), checkAI()]).catch(error => notify(error.message + ' Try Refresh.', true));

// Recheck a live connection; saved preferences and an earlier ready result are not health checks.
setInterval(checkAI, 15000);
window.addEventListener('focus', checkAI);
document.addEventListener('visibilitychange', () => { if (!document.hidden) checkAI(); });
