'use strict';
const $ = id => document.getElementById(id);
const state = { token: '', config: null, twins: [], twin: null, context: null, replay: false, result: null, job: null, plans: [] };
const won = value => value == null ? '확인 필요' : `${Number(value).toLocaleString('ko-KR')}원`;
const addDays = (day, n) => { const d = new Date(`${day}T00:00:00Z`); d.setUTCDate(d.getUTCDate()+n); return d.toISOString().slice(0, 10); };
const onDate = () => state.replay ? state.twin.as_of : state.config.today;
const el = (tag, text, cls) => { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; };
const show = (id, yes = true) => { $(id).hidden = !yes; };
const sample = value => value ? `${value.paths}개 경로 중 ${value.count}개` : '확인 필요';

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'X-Workbench-Token': state.token, ...(options.body ? {'Content-Type': 'application/json'} : {}), ...options.headers } });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || `요청 실패 (${response.status})`);
  return data;
}
function error(err) { $('error').textContent = err.message || String(err); show('error'); }
function table(target, headers, rows) {
  const node = el('table'); const head = el('thead'); const hr = el('tr');
  headers.forEach(text => hr.append(el('th', text))); head.append(hr); node.append(head);
  const body = el('tbody');
  rows.forEach(row => { const tr = el('tr'); row.forEach(value => tr.append(el('td', String(value ?? '확인 필요')))); body.append(tr); });
  node.append(body); $(target).replaceChildren(node);
}
function select(id, values, empty) {
  const previous = $(id).value; $(id).replaceChildren();
  if (empty) { const opt = el('option', empty); opt.value = ''; $(id).append(opt); }
  for (const [value, label] of values) { const opt = el('option', label); opt.value = value; $(id).append(opt); }
  if ([...$(id).options].some(o => o.value === previous)) $(id).value = previous;
}
function busy(yes) {
  document.querySelectorAll('button, input, select').forEach(node => { node.disabled = yes; });
  $('cancel').disabled = !yes; show('progress', yes);
  if (!yes) changeFields();
}
function requestBody(changes = []) {
  const body = { on_date: onDate(), through_date: $('through').value, replay: state.replay, changes };
  const reserve = $('reserve').value;
  if (reserve !== '' && Number(reserve) !== state.context.protected_cash_krw) body.protected_cash_krw = Number(reserve);
  return body;
}
async function run(changes = []) {
  if (!state.twin) return;
  show('error', false); busy(true); $('progress-text').textContent = '자료와 선택한 변경을 점검하고 있습니다.';
  try {
    const job = await api(`/api/twins/${state.twin.id}/coaching`, { method: 'POST', body: JSON.stringify(requestBody(changes)) });
    state.job = job.id;
    for (;;) {
      const current = await api(`/api/jobs/${job.id}`);
      if (current.status === 'succeeded') { state.result = await api(current.result_url); render(state.result); break; }
      if (current.status !== 'running') throw new Error(current.error?.message || '점검을 취소했습니다.');
      await new Promise(resolve => setTimeout(resolve, 250));
    }
  } catch (err) { error(err); }
  finally { state.job = null; busy(false); }
}
function render(result) {
  show('brief'); $('basis').textContent = `자료 ${result.as_of} · 점검 ${result.on_date} → ${result.through_date}`;
  show('replay-badge', result.context.replay);
  $('action-title').textContent = result.next_action.title; $('action-detail').textContent = result.next_action.detail;
  $('next-review').textContent = `다음 확인: ${result.next_check_in.date}. ${result.next_check_in.trigger}. 알림을 예약한 것은 아닙니다.`;
  show('update-data', result.status === 'needs_data' || result.next_action.kind === 'confirm_budget_inputs');
  show('replay', state.twin.as_of !== state.config.today);
  $('replay').textContent = state.replay ? '오늘 기준으로 돌아가기' : '자료 기준일로만 검토';
  show('choose-cap', result.next_action.kind === 'choose_spending_cap' || result.next_action.kind === 'reset_plan');
  show('decision-section', result.on_date === result.as_of);
  const projection = result.projection; const cash = projection?.cash;
  show('cash-section', Boolean(cash));
  if (cash) {
    $('cash-through').textContent = `${result.through_date}까지 · 조건부 모델`;
    $('cash-cards').replaceChildren();
    for (const account of cash.accounts) {
      const card = el('div', null, 'stat'); card.append(el('span', account.account_id));
      card.append(el('strong', won(account.additional_one_off_room?.p10_krw)));
      card.append(el('p', '추가 1회 지출 여유의 하위 10% 지점', 'muted'));
      card.append(el('p', `기간 중 계좌 부족: ${sample(account.period_shortfall)}`, 'muted'));
      $('cash-cards').append(card);
    }
    const b = cash.explanation;
    const names = { opening_cash_krw: '시작 잔액', income_krw: '+ 소득', reimbursement_krw: '+ 환급·정산', direct_spending_krw: '− 계좌·체크카드 소비', direct_fixed_krw: '− 계좌·체크카드 고정지출', card_settlement_krw: '− 신용카드 실제 정산', debt_service_krw: '− 채무 상환', savings_out_krw: '− 저축 이동', cash_withdrawal_krw: '− 현금 인출', rounding_adjustment_krw: '반올림 조정', terminal_cash_krw: '= 마지막 잔액' };
    table('cash-explanation', ['경로 평균 기준의 현금 흐름', '금액'], Object.entries(names).map(([key, title]) => [title, won(b[key])]));
  }
  show('upcoming-section', Boolean(projection));
  if (projection) {
    const types = { CARD_BILL: '카드 정산', PLANNED_EXPENSE: '내가 입력한 지출', RECURRING_INCOME: '입금 예상', RECURRING_EXPENSE: '반복 소비', RECURRING_FIXED_EXPENSE: '고정지출', RECURRING_SAVINGS_OUT: '저축 이동', RECURRING_DEBT_SERVICE: '채무 상환', RECURRING_INTERNAL_TRANSFER: '계좌 간 이동', RECURRING_REIMBURSEMENT: '정산·환급' };
    table('upcoming', ['날짜 / 종류', '금액 / 계좌', '근거'], projection.upcoming.slice(0, 20).map(row => [
      `${row.date} · ${types[row.event_type] || row.event_type}`, `${won(row.expected_amount_krw)} · ${row.account_id || row.card_id || ''}`, row.source]));
    if (!projection.upcoming.length) $('upcoming').replaceChildren(el('p', '등록되거나 추정된 일정이 없습니다. 예정 지출이 없다는 뜻은 아닙니다.', 'muted'));
    if (projection.upcoming.length > 20) $('upcoming').append(el('p', `전체 ${projection.upcoming.length}개 중 가까운 20개입니다. 전체 근거는 아래 JSON에서 확인할 수 있습니다.`, 'muted'));
  }
  const budgets = projection?.budgets || result.observed_budgets;
  show('budget-section', budgets.length > 0);
  $('budget-basis').textContent = projection ? `${projection.budget_cutoff_date || result.as_of}까지의 봉투 잔여입니다. 예산 잔여와 통장 잔액은 다릅니다.` : `${result.as_of}까지 관측된 사용액만 표시합니다. 오늘의 잔여 예산은 아닙니다.`;
  table('budgets', ['예산 항목', '관측 잔여', '예상 잔여 중간값 / 하위 10%'], budgets.map(row => [row.envelope, won(row.observed_remaining_krw), row.remaining_at_cutoff ? `${won(row.remaining_at_cutoff.p50_krw)} / ${won(row.remaining_at_cutoff.p10_krw)}` : '계산 보류']));
  show('comparison', Boolean(result.comparison));
  if (result.comparison) {
    const a = result.comparison.baseline.cash; const b = result.comparison.planned.cash; const e = result.comparison.effect;
    table('comparison-table', ['비교 항목', '변경 전', '변경 후'], [
      ['마지막 통장 잔액 중간값', won(a?.terminal_balance.p50_krw), won(b?.terminal_balance.p50_krw)],
      ['미결제액 차감 후 중간값', won(a?.terminal_unencumbered.p50_krw), won(b?.terminal_unencumbered.p50_krw)],
      ['기간 중 계좌 부족', sample(a?.period_account_shortfall), sample(b?.period_account_shortfall)],
      ['마지막 날 계좌 부족', sample(a?.terminal_account_shortfall), sample(b?.terminal_account_shortfall)],
      ['지출 변화 중간값', '동일 경로 비교', won(e.spending_change.p50_krw)],
      ['보관액까지 뺀 마지막 가용금액 변화', '동일 경로 비교', won(e.terminal_after_earmark_change?.p50_krw)]
    ]);
  }
  show('notes-section'); $('notes').replaceChildren();
  for (const warning of result.warnings) $('notes').append(el('p', warning.detail, 'note'));
  $('raw-result').textContent = JSON.stringify(result, null, 2);
  renderPlans();
}
function changeFields() {
  const kind = $('change-kind').value;
  document.querySelectorAll('[data-kinds]').forEach(label => {
    const visible = label.dataset.kinds.split(' ').includes(kind); label.hidden = !visible;
    label.querySelectorAll('input, select').forEach(input => { input.disabled = !visible; input.required = visible && input.id !== 'envelope'; });
  });
  const old = $('payer').value;
  const payers = (state.context?.accounts || []).map(a => [`account:${a.account_id}`, `계좌 ${a.account_id}`]);
  if (kind === 'expense') payers.push(...(state.context?.cards || []).map(c => [`card:${c.card_id}`, `${c.kind === 'CREDIT' ? '신용' : '체크'}카드 ${c.card_id}`]));
  select('payer', payers, '결제수단 선택'); if (payers.some(p => p[0] === old)) $('payer').value = old;
  const card = kind === 'expense' && $('payer').value.startsWith('card:');
  show('payment-field', card); $('payment-date').disabled = !card; $('payment-date').required = card;
  show('earmark-field', kind === 'expense' && !card); $('earmark').disabled = kind !== 'expense' || card;
  $('envelope').required = kind === 'spending_cap';
  show('save-plan', kind === 'spending_cap' && !state.replay);
  const notes = { expense: '지출일과 카드 출금일은 다를 수 있습니다. 신용카드는 실제 출금일을 직접 확인해 주세요.', set_aside: '돈을 쓰는 것이 아니라 해당 계좌에서 더 이상 사용할 수 없는 보관액으로 잡습니다. 실제 이체는 하지 않습니다.', income_delay: '선택한 입금 한 건의 금액은 유지하고 날짜만 옮깁니다. 일정이 없으면 자료에 확정 입금 일정을 먼저 등록해 주세요.', spending_cap: '시작일부터 종료일까지 추가로 쓸 총 한도입니다. 예정된 정기지출을 자동 취소하지 않으며, 실행 여부는 사용자가 결정합니다.' };
  $('change-note').textContent = notes[kind];
}
function changeInput() {
  const kind = $('change-kind').value; const c = { kind };
  if ($('label').value.trim()) c.label = $('label').value.trim();
  if (kind !== 'income_delay') {
    if ($('amount').value === '') throw new Error('실제 금액 또는 직접 정할 한도를 입력해 주세요.');
    c.amount_krw = Number($('amount').value);
  }
  if (kind === 'expense' || kind === 'set_aside') {
    const payer = $('payer').value; if (!payer) throw new Error('사용할 계좌 또는 카드를 선택해 주세요.');
    c[payer.startsWith('account:') ? 'account_id' : 'card_id'] = payer.slice(payer.indexOf(':')+1);
  }
  if (kind === 'expense') {
    c.date = $('event-date').value; if ($('envelope').value) c.envelope = $('envelope').value;
    if (c.card_id) c.payment_date = $('payment-date').value;
    else c.reserve_now = $('earmark').checked;
  } else if (kind === 'spending_cap') {
    Object.assign(c, { envelope: $('envelope').value, start_date: $('cap-start').value, end_date: $('cap-end').value });
  } else if (kind === 'income_delay') {
    const value = $('income').value;
    if (value === '') throw new Error('늦어지는 입금 한 건을 선택해 주세요.');
    const selected = state.context.income_occurrences[Number(value)];
    Object.assign(c, { rule_id: selected.rule_id, original_date: selected.date, new_date: $('income-new-date').value });
  }
  return c;
}
async function loadPlans() { state.plans = (await api(`/api/twins/${state.twin.id}/coaching/plans`)).plans; renderPlans(); }
function renderPlans() {
  show('plans-section', Boolean(state.twin)); $('plans').replaceChildren();
  if (!state.plans.length) { $('plans').append(el('p', '저장한 한도가 없습니다. 비교한 계획 중 직접 선택한 것만 기록합니다.', 'muted')); return; }
  const names = { needs_data: '추가 자료 필요', not_started: '시작 전', over_cap: '한도 초과 관측', within_cap: '관측 기간 한도 내', in_progress: '점검 중' };
  for (const plan of state.plans) {
    const progress = state.result?.follow_up.find(row => row.id === plan.id);
    const node = el('div', null, 'plan'); node.append(el('strong', plan.label));
    node.append(el('p', `${plan.action.start_date} ~ ${plan.action.end_date} · ${plan.action.envelope} · 한도 ${won(plan.action.amount_krw)}`, 'muted'));
    node.append(el('p', progress ? `${names[progress.state]} · 관측 사용 ${won(progress.observed_spending_krw)} · 자료 ${progress.observed_through}` : '새 자료로 점검하면 실제 사용액을 비교합니다.'));
    const button = el('button', '이 계획 기록 삭제', 'secondary'); button.type = 'button';
    button.addEventListener('click', async () => { if (!confirm('계획 기록을 삭제할까요? 거래 내역과 실제 돈에는 영향을 주지 않습니다.')) return; try { await api(`/api/twins/${state.twin.id}/coaching/plans/${plan.id}`, {method: 'DELETE'}); await loadPlans(); } catch (err) { error(err); } });
    node.append(button); $('plans').append(node);
  }
}
async function chooseTwin() {
  state.twin = state.twins.find(t => t.id === $('twin').value); state.replay = false; state.result = null;
  for (const id of ['brief', 'cash-section', 'upcoming-section', 'budget-section', 'decision-section', 'notes-section', 'comparison']) show(id, false);
  if (!state.twin) return;
  busy(true);
  try {
  state.context = await api(`/api/twins/${state.twin.id}/coaching/context`);
  $('through').value = addDays(state.config.today, 7);
  $('reserve').value = state.context.protected_cash_krw ?? '';
  dates(); select('income', state.context.income_occurrences.map((r, i) => [String(i), `${r.date} · ${won(r.amount_krw)} · ${r.source}`]), '입금 한 건 선택');
  changeFields(); await loadPlans(); await run();
  } finally { busy(false); }
}
function dates() { $('event-date').value = onDate(); $('cap-start').value = addDays(onDate(), 1); $('cap-end').value = $('through').value; $('payment-date').value = ''; }
$('review').addEventListener('click', () => run());
$('cancel').addEventListener('click', async () => { if (state.job) { try { await api(`/api/jobs/${state.job}/cancel`, {method: 'POST'}); } catch (err) { error(err); } } });
$('twin').addEventListener('change', () => chooseTwin().catch(error));
$('change-kind').addEventListener('change', changeFields); $('payer').addEventListener('change', changeFields);
$('replay').addEventListener('click', () => { state.replay = !state.replay; $('through').value = addDays(onDate(), 7); dates(); run(); });
$('choose-cap').addEventListener('click', () => { $('change-kind').value = 'spending_cap'; changeFields(); if (state.result.next_action.envelope) $('envelope').value = state.result.next_action.envelope; $('amount').focus(); $('decision-section').scrollIntoView({behavior: 'smooth'}); });
$('decision-form').addEventListener('submit', event => { event.preventDefault(); try { run([changeInput()]); } catch (err) { error(err); } });
$('save-plan').addEventListener('click', async () => {
  try {
    const action = changeInput();
    await api(`/api/twins/${state.twin.id}/coaching/plans`, {method: 'POST', body: JSON.stringify({label: action.label || `${action.envelope} · ${action.start_date}부터`, action, confirmed: true})});
    await loadPlans();
  } catch (err) { error(err); }
});
(async function boot() {
  try {
    const base = await api('/api/config'); state.token = base.token;
    state.config = await api('/api/coaching/config'); state.twins = (await api('/api/twins')).twins;
    select('envelope', state.config.envelopes.map(name => [name, name]), '미지정 (현금만 반영)');
    select('twin', state.twins.map(t => [t.id, `${t.name} · 자료 ${t.as_of}`]), state.twins.length ? null : '연결된 자료 없음');
    show('empty', !state.twins.length); $('review').disabled = !state.twins.length;
    if (state.twins.length) await chooseTwin();
  } catch (err) { error(err); }
})();
