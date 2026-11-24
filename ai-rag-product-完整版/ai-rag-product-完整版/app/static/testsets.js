'use strict';

const $ = id => document.getElementById(id);
const statusLabels = {answered: '有答案', no_answer: '拒答', handoff: '转人工', no_product: '未找到产品', product_found: '找到产品', error: '执行失败'};
const runLabels = {queued: '排队中', running: '运行中', completed: '已完成', failed: '失败'};
const retrieverLabels = {hash: '字符检索', bm25: 'BM25 + 字符重排', semantic: '语义检索'};
const answerLabels = {extractive: '原文回答', auto: 'FAQ 优先 / 原文', llm: 'LLM 生成'};
const pageSize = 20;
let group, products = [], runs = [], activeRun, editingCase, editingSet, resultPage = 0, pollTimer, viewRequest = 0, variantCounter = 0;
const rememberedSet = new URLSearchParams(location.search).get('set');

function element(tag, text, className) {
  const result = document.createElement(tag);
  if (text != null) result.textContent = text;
  if (className) result.className = className;
  return result;
}

function button(text, callback, className = 'secondary') {
  const result = element('button', text, className);
  result.type = 'button';
  result.onclick = callback;
  return result;
}

async function api(path, method = 'GET', data) {
  const options = {method};
  if (data instanceof FormData) options.body = data;
  else if (data !== undefined) {
    options.body = JSON.stringify(data);
    options.headers = {'Content-Type': 'application/json'};
  }
  const response = await fetch('/api' + path, options);
  const payload = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(payload.detail) ? payload.detail.map(item => item.loc.slice(1).join('.') + ' ' + item.msg).join('；') : payload.detail;
    throw new Error(detail || '请求失败');
  }
  return payload;
}

function notice(message, error = false) {
  $('notice').textContent = message;
  $('notice').hidden = !message;
  $('notice').classList.toggle('error', error);
}

function withNotice(action) {
  return async () => {
    try { await action(); }
    catch (error) { notice(error.message, true); }
  };
}

function fillTable(body, rows) {
  const fragment = document.createDocumentFragment();
  for (const values of rows) {
    const row = element('tr');
    for (const value of values) {
      const cell = element('td');
      cell.append(value instanceof Node ? value : document.createTextNode(String(value)));
      row.append(cell);
    }
    fragment.append(row);
  }
  body.replaceChildren(fragment);
}

function table(headers, rows) {
  const wrap = element('div', null, 'table-scroll');
  const result = element('table'), headings = result.createTHead().insertRow();
  for (const label of headers) {
    const header = element('th', label);
    header.scope = 'col';
    headings.append(header);
  }
  fillTable(result.createTBody(), rows);
  wrap.append(result);
  return wrap;
}

function groupPath(id = group?.id) { return '/test-sets/' + encodeURIComponent(id); }
function runPath(id) { return '/test-runs/' + encodeURIComponent(id); }
function productName(id) { return products.find(product => product.id === id)?.name || id; }
function percent(value) { return value == null ? '—' : (value * 100).toFixed(1) + '%'; }
function point(value) { return value == null ? '—' : Number(value).toFixed(1); }
function listItems(value) { return value.split(/\r?\n/).map(line => line.trim()).filter(Boolean); }

async function refreshSets(preferred) {
  const request = ++viewRequest;
  const sets = await api('/test-sets');
  const selected = sets.find(set => set.id === preferred) || sets[0];
  const next = selected ? await api(groupPath(selected.id)) : null;
  if (request !== viewRequest) return;
  group = next;
  const url = new URL(location.href);
  if (group) url.searchParams.set('set', group.id);
  else url.searchParams.delete('set');
  history.replaceState(null, '', url);
  $('setList').replaceChildren(...sets.map(set => {
    const entry = button(set.name, withNotice(() => refreshSets(set.id)), 'set-entry' + (group?.id === set.id ? '' : ' secondary'));
    if (group?.id === set.id) entry.setAttribute('aria-current', 'true');
    entry.append(element('small', set.case_count + ' 条用例'));
    return entry;
  }));
  if (!sets.length) $('setList').append(element('p', '还没有测试集。', 'muted'));
  $('setPanel').hidden = !group;
  $('runPanel').hidden = !group;
  if (group) {
    $('setName').textContent = group.name + ' · ' + group.cases.length + ' 条';
    $('setDescription').textContent = group.description;
    $('setDescription').hidden = !group.description;
    $('caseEmpty').hidden = Boolean(group.cases.length);
    $('runButton').disabled = !group.cases.length;
    fillTable($('caseRows'), group.cases.map(item => {
      const actions = element('div', null, 'actions');
      actions.append(button('编辑', () => openCase(item)),
        button('删除', withNotice(async () => {
          if (!confirm('删除此用例？历史评测记录会保留。')) return;
          const id = group.id;
          await api(groupPath(id) + '/cases/' + item.id, 'DELETE');
          await refreshSets(id);
          notice('用例已删除。');
        }), 'secondary danger'));
      const markup = [item.expected_answer && '参考答案', item.expected_terms.length && '关键词', (item.expected_source || item.expected_evidence) && '证据', item.history.length && '多轮'].filter(Boolean).join(' · ');
      return [item.question, item.product_id ? productName(item.product_id) + ' / ' + item.version : '—', statusLabels[item.expected_status], markup || '—', actions];
    }));
  }
  await refreshRuns();
}

function openSet(item = null) {
  editingSet = item?.id;
  $('setDialogTitle').textContent = item ? '编辑测试集' : '新建测试集';
  $('groupName').value = item?.name || '';
  $('groupDescription').value = item?.description || '';
  openDialog('setDialog');
}

function updateCaseVersions(preferred = '') {
  const product = products.find(item => item.id === $('caseProduct').value);
  const values = [...(product?.versions || [])];
  if (preferred && !values.includes(preferred)) values.push(preferred);
  $('caseVersion').replaceChildren(new Option('请选择版本', ''), ...values.map(value => new Option(value, value)));
  $('caseVersion').value = preferred || values[0] || '';
}

async function openCase(item = null) {
  try {
    products = await api('/products');
    const options = products.map(product => new Option(product.name + ' · ' + product.id, product.id));
    if (item?.product_id && !products.some(product => product.id === item.product_id)) {
      options.push(new Option(item.product_id, item.product_id));
    }
    $('caseProduct').replaceChildren(new Option('不限定产品', ''), ...options);
    $('caseProduct').value = item?.product_id || products[0]?.id || '';
    if (item && !item.product_id) $('caseProduct').value = '';
    updateCaseVersions(item?.version);
    editingCase = item?.id;
    $('caseDialogTitle').textContent = item ? '编辑用例' : '添加用例';
    $('caseQuestion').value = item?.question || '';
    $('caseStatus').value = item?.expected_status || 'answered';
    $('caseAnswer').value = item?.expected_answer || '';
    $('caseTerms').value = (item?.expected_terms || []).join('\n');
    $('caseSource').value = item?.expected_source || '';
    $('caseEvidence').value = item?.expected_evidence || '';
    $('caseHistory').value = (item?.history || []).join('\n');
    openDialog('caseDialog');
  } catch (error) { notice(error.message, true); }
}

function openDialog(id) {
  const dialog = $(id);
  dialog.querySelector('.form-error').textContent = '';
  dialog.showModal();
}

function bindForm(id, submit) {
  $(id).addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget, submitButton = form.querySelector('button[type="submit"]');
    const message = form.querySelector('.form-error');
    message.textContent = '';
    submitButton.disabled = true;
    try { await submit(form); }
    catch (error) { message.textContent = error.message; }
    finally { submitButton.disabled = false; }
  });
}

function labeledField(parent, token, key, label, field) {
  field.id = 'variant-' + token + '-' + key;
  field.dataset.key = key;
  const wrap = element('div'), caption = element('label', label);
  caption.htmlFor = field.id;
  wrap.append(caption, field);
  parent.append(wrap);
  return wrap;
}

function variantControl(options) {
  const input = element('select');
  input.replaceChildren(...Object.entries(options).map(([value, label]) => new Option(label, value)));
  return input;
}

function syncVariants() {
  const variants = [...$('variants').children];
  variants.forEach((item, index) => {
    item.querySelector('legend').textContent = index === 0 ? '方案 1 · 本次基线' : '方案 ' + (index + 1);
    item.querySelector('.remove-variant').hidden = variants.length === 1;
  });
  $('addVariant').disabled = variants.length >= 4;
}

function addVariant(saved) {
  const index = $('variants').children.length, token = ++variantCounter, fieldset = element('fieldset', null, 'variant');
  fieldset.append(element('legend'));
  const name = element('input');
  name.value = saved?.name || (index ? '方案 ' + (index + 1) : '字符基线');
  name.required = true;
  name.maxLength = 80;
  const retriever = variantControl(retrieverLabels), mode = variantControl(answerLabels), model = element('input'), threshold = element('input');
  model.placeholder = '留空使用已配置模型';
  model.maxLength = 200;
  Object.assign(threshold, {type: 'number', min: '0', max: '1', step: 'any', placeholder: '默认 0.16'});
  labeledField(fieldset, token, 'name', '名称', name);
  labeledField(fieldset, token, 'retriever', '检索器', retriever);
  labeledField(fieldset, token, 'answer_mode', '回答模式', mode);
  labeledField(fieldset, token, 'threshold', '检索阈值', threshold);
  const modelWrap = labeledField(fieldset, token, 'model', '模型 ID · 当前服务商', model);
  mode.onchange = () => { modelWrap.hidden = mode.value !== 'llm'; };
  retriever.onchange = () => { threshold.placeholder = retriever.value === 'semantic' ? '默认 0.35' : '默认 0.16'; };
  retriever.value = saved?.retriever || 'hash';
  mode.value = saved?.answer_mode || 'extractive';
  model.value = saved?.model || '';
  threshold.value = saved?.threshold ?? '';
  retriever.onchange();
  mode.onchange();
  fieldset.append(button('移除方案', () => { fieldset.remove(); syncVariants(); }, 'secondary remove-variant'));
  $('variants').append(fieldset);
  syncVariants();
}

async function refreshRuns() {
  const groupId = group?.id, all = $('allRuns').checked;
  const data = await api('/test-runs' + (groupId && !all ? '?set_id=' + encodeURIComponent(groupId) : ''));
  if (groupId !== group?.id || all !== $('allRuns').checked) return;
  runs = data;
  const baseline = $('baseline').value;
  $('baseline').replaceChildren(new Option('不选择历史基线', ''), ...runs.filter(run => run.status === 'completed' && run.set_id === groupId)
    .flatMap(run => run.config.variants.map((variant, index) => new Option(run.created_at + ' · ' + variant.name, run.id + ':' + index))));
  if ([...$('baseline').options].some(option => option.value === baseline)) $('baseline').value = baseline;
  if (!runs.length) {
    $('runList').replaceChildren(element('p', '暂无评测记录。', 'muted'));
    return;
  }
  $('runList').replaceChildren(table(['运行时间', '测试集', '状态', '方案 / 用例', '操作'], runs.map(run => {
    const actions = element('div', null, 'actions');
    actions.append(button('查看', withNotice(() => openRun(run.id, true))));
    if (run.status === 'completed' && run.set_id === groupId) actions.append(button('复用方案', () => {
      $('variants').replaceChildren();
      run.config.variants.forEach(addVariant);
      $('baseline').value = run.id + ':0';
      $('remoteConsent').checked = false;
      $('runPanel').scrollIntoView({behavior: 'smooth', block: 'start'});
    }));
    return [run.created_at, run.set_name, runLabels[run.status], run.config.variants.length + ' / ' + run.config.case_count, actions];
  })));
}

function isRunning(run) { return ['queued', 'running'].includes(run.status); }

async function openRun(id, reveal = false) {
  clearTimeout(pollTimer);
  const initial = reveal || activeRun?.id !== id;
  const requestedId = id;
  activeRun = {id};
  const run = await api(runPath(id));
  if (activeRun?.id !== requestedId) return;
  activeRun = run;
  renderRun(run, initial);
  if (reveal) $('resultPanel').scrollIntoView({behavior: 'smooth', block: 'start'});
  if (isRunning(run)) {
    pollTimer = setTimeout(() => openRun(id).catch(error => {
      $('runStatus').textContent = '读取运行状态失败：' + error.message;
      pollTimer = setTimeout(withNotice(() => openRun(id)), 3000);
    }), 2200);
  } else {
    await refreshRuns();
  }
}

function renderMetrics(run) {
  const header = ['方案', '通过', '严格通过', '状态准确率', '自动均分', '人工均分', 'Hit@1', 'Hit@3', 'Hit@5', 'MRR@5', '引用命中', '拒答准确率', '平均耗时'];
  const rows = run.variants.map(({variant, metrics}) => {
    const config = element('div');
    config.append(element('strong', variant.name),
      element('p', [retrieverLabels[variant.retriever], answerLabels[variant.answer_mode], variant.model, variant.embedding_model, '阈值 ' + variant.threshold].filter(Boolean).join(' · '), 'muted'),
      element('p', '证据标注 ' + metrics.evidence_cases + ' 条', 'muted'));
    const models = Object.entries(metrics.observed_models || {});
    if (models.length) config.append(element('p', '实际模型：' + models.map(([name, count]) => name + ' × ' + count).join('、'), 'muted'));
    return [config, metrics.passed + '/' + metrics.total,
      metrics.strict_passed == null ? '—' : metrics.strict_passed + '/' + metrics.total,
      percent(metrics.status_accuracy), point(metrics.average_score),
      point(metrics.human_average_score) + '（' + metrics.reviewed_cases + '）',
      ...['1', '3', '5'].map(k => percent(metrics.hit_at_k[k])),
      metrics.mrr_at_5 ?? '—', percent(metrics.citation_accuracy), percent(metrics.refusal_accuracy), point(metrics.mean_latency_ms) + ' ms'];
  });
  $('metrics').replaceChildren(table(header, rows),
    element('p', '自动分统一格式和常见等价单位，严格口径逐字匹配。两者均核对状态，人工分单独汇总。检索指标只统计已标注证据的用例。', 'muted'));
}

function renderComparisons(comparisons) {
  $('comparisons').replaceChildren();
  for (const item of comparisons) {
    const group = element('details', null, 'comparison');
    group.append(element('summary', item.baseline + ' → ' + item.candidate + ' · 退步 ' + item.regressions.length + ' · 改善 ' + item.improvements.length),
      element('p', '可比较 ' + item.comparable + ' 题。新增 ' + item.new.length + '，移除 ' + item.removed.length + '，标注变更 ' + item.edited.length + '。' +
        (item.same_corpus === false ? ' 两次运行的知识资料快照有变化。' : '') +
        (item.comparison_scoring === 'literal' ? ' 两次评分版本不同，回归采用严格口径。' : ''), 'muted'));
    const types = {regressions: '通过变未通过', improvements: '未通过变通过', score_drops: '分数下降', score_gains: '分数提升', retrieval_drops: '证据排名下降', edited: '标注变更', new: '新增', removed: '移除'};
    for (const [key, label] of Object.entries(types)) {
      if (!item[key].length) continue;
      const list = element('ul');
      for (const row of item[key]) {
        const hasComparison = row.before_score != null && row.after_score != null && key !== 'edited';
        list.append(element('li', row.question + (hasComparison ? ' · 分数 ' + row.before_score + ' → ' + row.after_score +
          ' · 排名 ' + (row.before_rank ?? '—') + ' → ' + (row.after_rank ?? '—') : '')));
      }
      group.append(element('h3', label), list);
    }
    $('comparisons').append(group);
  }
}

function renderRun(run, initial) {
  $('resultPanel').hidden = false;
  $('resultTitle').textContent = run.set_name + ' · 评测结果';
  $('runStatus').textContent = run.created_at + ' · ' + runLabels[run.status] + (run.error ? ' · ' + run.error : '');
  const completed = run.status === 'completed';
  $('reportLinks').hidden = !completed;
  $('resultTools').hidden = !completed;
  if (!completed) {
    for (const id of ['metrics', 'comparisons', 'results']) $(id).replaceChildren();
    $('resultPages').hidden = true;
    return;
  }
  $('markdownReport').href = '/api' + runPath(run.id) + '/report';
  $('jsonReport').href = '/api' + runPath(run.id) + '/report?format=json';
  if (initial || !$('resultVariant').options.length) {
    $('resultVariant').replaceChildren(...run.variants.map(({variant}) => new Option(variant.name, variant.id)));
    $('resultFilter').value = 'all';
    resultPage = 0;
  } else if (!run.variants.some(item => item.variant.id === $('resultVariant').value)) {
    $('resultVariant').replaceChildren(...run.variants.map(({variant}) => new Option(variant.name, variant.id)));
  }
  renderMetrics(run);
  renderComparisons(run.comparisons);
  renderResults();
}

function renderEvidence(detail, title, sources) {
  detail.append(element('h3', title));
  if (!sources.length) {
    detail.append(element('p', '无。', 'muted'));
    return;
  }
  for (const [index, source] of sources.entries()) {
    detail.append(element('p', (index + 1) + '. ' + source.source + (source.score == null ? '' : ' · 相似度 ' + source.score), 'muted'),
      element('p', source.text, 'evidence-text'));
  }
}

function reviewForm(run, item, row) {
  const form = element('form', null, 'review-form'), scoreLabel = element('label', '人工评分'), noteLabel = element('label', '点评');
  const score = element('input'), note = element('input'), submit = element('button', '保存评分');
  Object.assign(score, {type: 'number', min: '0', max: '100', step: '1', required: true, value: row.review?.score ?? ''});
  note.value = row.review?.note || '';
  note.maxLength = 2000;
  scoreLabel.append(score);
  noteLabel.append(note);
  form.append(scoreLabel, noteLabel, submit);
  const status = element('p', row.review ? '已保存人工评分。' : '', 'muted');
  status.setAttribute('role', 'status');
  form.onsubmit = async event => {
    event.preventDefault();
    submit.disabled = true;
    try {
      row.review = await api(runPath(run.id) + '/variants/' + item.variant.id + '/cases/' + row.case.id + '/review',
        'PUT', {score: Number(score.value), note: note.value});
      const scores = item.rows.filter(entry => entry.review).map(entry => entry.review.score);
      item.metrics.reviewed_cases = scores.length;
      item.metrics.human_average_score = scores.reduce((sum, value) => sum + value, 0) / scores.length;
      if (activeRun?.id === run.id) renderMetrics(run);
      status.textContent = '已保存人工评分。';
    } catch (error) { status.textContent = error.message; }
    finally { submit.disabled = false; }
  };
  return [form, status];
}

function resultDetail(run, item, row) {
  const detail = element('details', null, 'result-item'), summary = element('summary');
  summary.append(element('span', (row.passed ? '通过' : '未通过') + ' · ' + row.score + ' 分', 'badge ' + (row.passed ? 'pass' : 'fail')),
    document.createTextNode(row.case.question));
  detail.append(summary);
  detail.addEventListener('toggle', () => {
    if (!detail.open || detail.dataset.loaded) return;
    detail.dataset.loaded = 'true';
    const test = row.case;
    detail.append(element('p', '预期状态 ' + statusLabels[test.expected_status] + ' · 实际状态 ' + (statusLabels[row.actual_status] || row.actual_status) +
      ' · ' + row.latency_ms + ' ms' + (row.generation_mode ? ' · ' + row.generation_mode : '') +
      (row.model_used ? ' · ' + row.model_used : '') +
      (row.generation_error ? ' · ' + row.generation_error : ''), 'muted'));
    if (test.product_id) detail.append(element('p', '预期范围 ' + productName(test.product_id) + ' / ' + test.version + ' · 实际范围 ' +
      (row.actual_product_id ? productName(row.actual_product_id) + ' / ' + (row.actual_version || '—') : '—'), 'muted'));
    if (row.reasons.length) detail.append(element('p', row.reasons.map(reason => run.failure_labels[reason]).join('；'), 'error'));
    if (row.strict_score != null) detail.append(element('p', '等价口径 ' + row.score + ' 分 · 严格口径 ' + row.strict_score + ' 分', 'muted'));
    if (row.retrieval_candidates != null) detail.append(element('p', '重排候选 ' + row.retrieval_candidates + ' 条' + (row.retrieval_fallback ? ' · 已回退字符检索' : ''), 'muted'));
    detail.append(element('h3', '实际回答'), element('pre', row.actual_answer || '无'));
    if (test.expected_answer) detail.append(element('h3', '参考答案'), element('pre', test.expected_answer));
    if (test.history.length) detail.append(element('p', '前置问句：' + test.history.join(' → '), 'muted'));
    if (test.expected_terms.length) detail.append(element('p', '关键词：' + test.expected_terms.join('、') + ' · 缺失：' + (row.missing_terms.join('、') || '无'), 'muted'));
    if (row.evidence_labeled) detail.append(element('p', '标注证据：' + [test.expected_source, test.expected_evidence].filter(Boolean).join(' · ') +
      ' · 排名 ' + (row.evidence_rank ?? '未命中'), 'muted'));
    renderEvidence(detail, '回答引用', row.sources);
    renderEvidence(detail, '检索结果 · Top 5', row.retrieved);
    detail.append(...reviewForm(run, item, row));
  });
  return detail;
}

function renderResults() {
  const run = activeRun;
  if (run?.status !== 'completed') return;
  const item = run.variants.find(entry => entry.variant.id === $('resultVariant').value) || run.variants[0];
  const filter = $('resultFilter').value;
  const rows = item.rows.filter(row => filter === 'all' || (filter === 'failed' && !row.passed) ||
    (filter === 'passed' && row.passed) || (filter === 'diagnostic' && row.reasons.length));
  const start = resultPage * pageSize;
  const counts = Object.entries(item.metrics.reason_counts).sort((left, right) => right[1] - left[1]);
  $('results').replaceChildren();
  if (counts.length) $('results').append(element('p', '诊断分布：' + counts.map(([reason, count]) => run.failure_labels[reason] + ' ' + count).join(' · '), 'muted'));
  $('results').append(...rows.slice(start, start + pageSize).map(row => resultDetail(run, item, row)));
  if (!rows.length) $('results').append(element('p', '没有符合条件的结果。', 'muted'));
  $('resultCount').textContent = rows.length ? (start + 1) + '–' + Math.min(start + pageSize, rows.length) + ' / ' + rows.length : '0 条';
  $('resultPages').hidden = rows.length <= pageSize;
  $('previousResults').disabled = !resultPage;
  $('nextResults').disabled = start + pageSize >= rows.length;
}

for (const close of document.querySelectorAll('[data-close]')) close.onclick = () => $(close.dataset.close).close();
$('caseStatus').replaceChildren(...Object.entries(statusLabels).filter(([value]) => value !== 'error').map(([value, label]) => new Option(label, value)));
$('caseProduct').onchange = () => updateCaseVersions();
$('newSet').onclick = () => openSet();
$('editSet').onclick = () => openSet(group);
$('newCase').onclick = () => openCase();
$('openImport').onclick = () => { $('importForm').reset(); openDialog('importDialog'); };
$('deleteSet').onclick = withNotice(async () => {
  if (!confirm('删除测试集及其用例？历史评测记录会保留。')) return;
  await api(groupPath(), 'DELETE');
  await refreshSets();
  notice('测试集已删除，可在全部评测记录中查看历史结果。');
});
$('addVariant').onclick = () => addVariant();
$('refreshRuns').onclick = withNotice(refreshRuns);
$('allRuns').onchange = withNotice(refreshRuns);
for (const id of ['resultVariant', 'resultFilter']) $(id).onchange = () => { resultPage = 0; renderResults(); };
$('previousResults').onclick = () => { resultPage--; renderResults(); };
$('nextResults').onclick = () => { resultPage++; renderResults(); };

bindForm('setForm', async () => {
  const data = {name: $('groupName').value, description: $('groupDescription').value};
  const saved = await api(editingSet ? groupPath(editingSet) : '/test-sets', editingSet ? 'PUT' : 'POST', data);
  $('setDialog').close();
  await refreshSets(saved.id);
  notice('测试集已保存。');
});
bindForm('caseForm', async () => {
  const id = group.id;
  const data = {question: $('caseQuestion').value, product_id: $('caseProduct').value, version: $('caseVersion').value,
    expected_status: $('caseStatus').value, expected_answer: $('caseAnswer').value, expected_terms: listItems($('caseTerms').value),
    expected_source: $('caseSource').value, expected_evidence: $('caseEvidence').value, history: listItems($('caseHistory').value)};
  await api(groupPath(id) + '/cases' + (editingCase ? '/' + editingCase : ''), editingCase ? 'PUT' : 'POST', data);
  $('caseDialog').close();
  await refreshSets(id);
  notice('用例已保存。');
});
bindForm('importForm', async () => {
  const id = group.id, form = new FormData();
  form.append('file', $('importFile').files[0]);
  const result = await api(groupPath(id) + '/import', 'POST', form);
  $('importDialog').close();
  await refreshSets(id);
  notice('成功导入 ' + result.imported + ' 条用例。');
});
$('runForm').onsubmit = async event => {
  event.preventDefault();
  $('runButton').disabled = true;
  try {
    const variants = [...$('variants').children].map(item => Object.fromEntries(
      [...item.querySelectorAll('[data-key]')].map(field => [field.dataset.key,
        field.dataset.key === 'threshold' ? (field.value === '' ? null : Number(field.value)) : field.value])));
    const [baselineId, baselineVariant] = $('baseline').value.split(':');
    const result = await api(groupPath() + '/runs', 'POST', {variants, baseline_run_id: baselineId || null,
      baseline_variant: Number(baselineVariant || 0), allow_remote: $('remoteConsent').checked});
    notice('已提交批量评测。');
    await openRun(result.id, true);
    await refreshRuns();
  } catch (error) { notice(error.message, true); }
  finally { $('runButton').disabled = !group?.cases.length; }
};

addVariant();
withNotice(async () => {
  products = await api('/products');
  await refreshSets(rememberedSet);
})();
