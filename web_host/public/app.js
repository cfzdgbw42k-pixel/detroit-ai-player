const app = document.querySelector('#app');
const toast = document.querySelector('#toast');
let active = null;
let selectedChoice = null;

const escapeHtml = (value = '') => String(value)
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;').replaceAll("'", '&#039;');

function notify(message) {
  toast.textContent = message;
  toast.classList.add('show');
  window.setTimeout(() => toast.classList.remove('show'), 1800);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {'Content-Type': 'application/json'},
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '操作失敗');
  return data;
}

function frame(content, status = 'LOCAL / BLIND') {
  return `
    <header class="topbar">
      <div class="brand"><div class="mark">DB</div><div><strong>DETROIT BLIND RUN</strong><small>多人盲玩｜本機主持台</small></div></div>
      <div class="status">${escapeHtml(status)}</div>
    </header>
    ${content}`;
}

async function showHome() {
  active = null;
  const {sessions, ending_progress: endingProgress} = await api('/api/sessions');
  const cards = sessions.length ? sessions.map(save => `
    <div class="save-card">
      <div><strong>${escapeHtml(save.name)}</strong><span>${save.complete ? '全周目完成' : `第 ${save.chapter_index + 1}／${save.chapter_count} 章`} · ${difficultyName(save.difficulty)}</span></div>
      <div class="save-actions">
        <button class="button" data-open="${escapeHtml(save.id)}">繼續</button>
        <button class="button" data-history="${escapeHtml(save.id)}">查看記錄卡</button>
        <button class="button danger" data-delete="${escapeHtml(save.id)}" data-name="${escapeHtml(save.name)}">刪除</button>
      </div>
    </div>`).join('') : '<p class="empty">還沒有存檔。替玩家建立一個獨立周目，就能從第一章開始。</p>';
  app.innerHTML = frame(`
    <div class="home-grid">
      <section class="panel home-copy">
        <div class="eyebrow">32 Chapters · No Walkthrough</div>
        <h1>每一個選擇，都留下理由。</h1>
        <div class="rule"></div>
        <p class="lede">頁面只把當前場景交給玩家。選擇、理由與章末記錄會自動保存；成功率、隱藏條件和未來分支不會出現在盲玩內容裡。</p>
        <section class="ending-progress" aria-label="全結局完成度">
          <div class="progress-heading"><strong>全結局完成度</strong><span>${endingProgress.discovered}／${endingProgress.total} · ${endingProgress.percent}%</span></div>
          <div class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${endingProgress.percent}" aria-label="已完成 ${endingProgress.percent}%">
            <div class="progress-fill" style="width: ${Math.min(100, Math.max(0, endingProgress.percent))}%"></div>
          </div>
          <p>統計這台電腦上的所有周目；重複結局只算一次，未發現結局不顯示名稱。</p>
        </section>
        <div class="save-list"><h3>已有存檔</h3>${cards}</div>
      </section>
      <section class="panel new-save">
        <div class="eyebrow">New Campaign</div>
        <h2>建立獨立周目</h2>
        <p class="lede">每位玩家使用不同存檔，彼此的選擇不會洩漏。</p>
        <div class="field"><label for="save-name">存檔名稱</label><input id="save-name" value="玩家・容容線" maxlength="60"></div>
        <div class="field"><label for="difficulty">難度</label><select id="difficulty"><option value="casual">休閒</option><option value="experienced">熟練</option><option value="hardcore">困難</option></select></div>
        <button id="create" class="button primary wide">從第一章開始</button>
        <div class="field import-field"><label for="import-file">換電腦或重裝後恢復</label><input id="import-file" type="file" accept="application/json,.json"><button id="import" class="button wide">匯入完整存檔</button></div>
      </section>
    </div>`);
  document.querySelector('#create').addEventListener('click', createSession);
  document.querySelector('#import').addEventListener('click', importBackup);
  document.querySelectorAll('[data-open]').forEach(button => button.addEventListener('click', () => loadSession(button.dataset.open)));
  document.querySelectorAll('[data-history]').forEach(button => button.addEventListener('click', () => showHistory(button.dataset.history)));
  document.querySelectorAll('[data-delete]').forEach(button => button.addEventListener('click', () => deleteSession(button.dataset.delete, button.dataset.name)));
}

async function deleteSession(id, name) {
  if (!window.confirm(`確定要刪除「${name}」嗎？\n\n這會刪除本機遊玩進度與自動備份，無法復原。`)) return;
  const confirmation = window.prompt(`最後確認：請輸入「刪除」以永久刪除「${name}」。`);
  if (confirmation !== '刪除') return notify('已取消刪除');
  try {
    await api('/api/delete', {method: 'POST', body: JSON.stringify({id, name, confirmation})});
    await showHome();
    notify(`已刪除「${name}」`);
  } catch (error) { notify(error.message); }
}

function decisionCards(decisions = []) {
  return decisions.length ? decisions.map((item, index) => `
    <div class="record">
      <div class="tiny">決策 ${index + 1}${item.random_event ? ` · 隨機事件：${escapeHtml(item.resolution)}` : ''}</div>
      <p class="record-choice">${escapeHtml(item.selected_text)}</p>
      <p class="record-reason">理由：${escapeHtml(item.reason || '未填理由')}</p>
    </div>`).join('') : '<p class="empty">本章尚未留下選擇。</p>';
}

async function showHistory(id) {
  try {
    const data = await api(`/api/history?id=${encodeURIComponent(id)}`);
    const chapters = data.chapters.map(item => `
      <details class="history-chapter">
        <summary><span>第 ${item.chapter_number} 章・${escapeHtml(item.title)}</span><small>${escapeHtml([...new Set(item.all_endings.map(ending => ending.title))].join('／'))}</small></summary>
        <div class="history-body">
          ${decisionCards(item.decisions.filter(decision => decision.selected_text))}
          ${item.reflection ? `<div class="reflection"><div class="tiny">玩家的章末回顧</div><p>${escapeHtml(item.reflection)}</p></div>` : ''}
        </div>
      </details>`).join('');
    const current = data.current ? `
      <details class="history-chapter" open>
        <summary><span>第 ${data.current.chapter_number} 章・${escapeHtml(data.current.title)}</span><small>進行中</small></summary>
        <div class="history-body">${decisionCards(data.current.decisions)}</div>
      </details>` : '';
    const skipped = data.skipped_chapters.map(item => `<div class="record"><div class="tiny">第 ${item.chapter_number} 章 · 未進入</div><p class="record-choice">${escapeHtml(item.title)}</p><p class="record-reason">${escapeHtml(item.reason)}</p></div>`).join('');
    app.innerHTML = frame(`
      <article class="panel ending history-page">
        <div class="eyebrow">Record Cards</div>
        <h1>${escapeHtml(data.session.name)}</h1>
        <p class="lede">所有已完成章節的選擇、理由與回顧都在這裡；進行中的本章也會列出已保存的決定。</p>
        <div class="history-list">${chapters}${current}${skipped || (!chapters && !current ? '<p class="empty">目前還沒有遊玩記錄。</p>' : '')}</div>
        <div class="answer-actions"><button id="history-continue" class="button primary">繼續遊玩</button><button id="home" class="button ghost">回存檔首頁</button></div>
      </article>`, 'RECORDS');
    document.querySelector('#history-continue').addEventListener('click', () => loadSession(id));
    document.querySelector('#home').addEventListener('click', showHome);
  } catch (error) { notify(error.message); }
}

async function importBackup() {
  const file = document.querySelector('#import-file').files[0];
  if (!file) return notify('請先選擇完整存檔檔案');
  try {
    const backup = JSON.parse(await file.text());
    render(await api('/api/import', {method: 'POST', body: JSON.stringify({backup})}));
    notify('完整存檔已恢復');
  } catch (error) { notify(error.message || '無法讀取這份存檔'); }
}

function difficultyName(value) {
  return ({casual: '休閒', experienced: '熟練', hardcore: '困難'})[value] || value;
}

async function createSession() {
  const name = document.querySelector('#save-name').value.trim() || '玩家・容容線';
  const difficulty = document.querySelector('#difficulty').value;
  try { render(await api('/api/sessions', {method: 'POST', body: JSON.stringify({name, difficulty})})); }
  catch (error) { notify(error.message); }
}

async function loadSession(id) {
  try { render(await api(`/api/session?id=${encodeURIComponent(id)}`)); }
  catch (error) { notify(error.message); }
}

function render(data) {
  active = data;
  selectedChoice = null;
  if (data.screen === 'scene') return renderScene(data);
  if (data.screen === 'chapter_complete') return renderEnding(data);
  if (data.screen === 'campaign_complete') return renderCampaignComplete(data);
}

function sidebar(data) {
  return `<aside class="panel sidebar">
    <section><div class="eyebrow">Save</div><h3>${escapeHtml(data.session.name)}</h3><p>第 ${data.chapter.number}／${data.session.chapter_count} 章<br>${difficultyName(data.session.difficulty)}難度 · 已記錄 ${data.decision_count} 幕</p></section>
    <section><button id="copy" class="button primary">複製情節</button><button id="export" class="button">匯出對話交接卡</button><button id="backup" class="button">匯出完整存檔</button><button id="home" class="button ghost">回存檔首頁</button></section>
    <section><p class="tiny">只會複製玩家目前能知道的內容，不包含成功率、隱藏變數或後續答案。</p></section>
  </aside>`;
}

function renderScene(data) {
  const choices = data.choices.map(choice => `
    <button class="choice" data-choice="${escapeHtml(choice.id)}" data-label="${choice.label}" aria-pressed="false">
      <span class="choice-key">${choice.label}</span><span>${escapeHtml(choice.text)}</span>
    </button>`).join('');
  const actionArea = data.choices.length ? `
    <div id="answer-box" class="answer-box">
      <label for="answer">貼上玩家的回答，頁面會保存選項與理由</label>
      <textarea id="answer" placeholder="選擇：B｜理由：……"></textarea>
      <p id="parsed" class="tiny">尚未辨認選項</p>
      <div class="answer-actions"><button id="parse" class="button">辨認回答</button><button id="confirm" class="button primary" disabled>確認並前進</button></div>
    </div>` : `<button id="continue" class="button primary">讀完，前往下一幕</button>`;
  app.innerHTML = frame(`
    <div class="game-layout">
      <article class="panel scene">
        <div class="chapter-line"><span>第 ${data.chapter.number} 章 · ${escapeHtml(data.chapter.title)}</span><span>${escapeHtml(Array.isArray(data.chapter.protagonist) ? data.chapter.protagonist.join(' / ') : data.chapter.protagonist)}</span></div>
        <div class="eyebrow">Current Scene</div>
        <div class="scene-text">${escapeHtml(data.context)}</div>
        ${data.choices.length ? `<div class="choices">${choices}</div>` : ''}
        ${actionArea}
      </article>
      ${sidebar(data)}
    </div>`, 'STORY ACTIVE');
  bindCommon(data);
  if (data.choices.length) {
    document.querySelectorAll('[data-choice]').forEach(button => button.addEventListener('click', () => select(button.dataset.choice)));
    document.querySelector('#parse').addEventListener('click', parseAnswer);
    document.querySelector('#answer').addEventListener('input', parseAnswerSilent);
    document.querySelector('#confirm').addEventListener('click', confirmChoice);
  } else {
    document.querySelector('#continue').addEventListener('click', () => perform('continue'));
  }
}

function select(id) {
  selectedChoice = id;
  document.querySelectorAll('[data-choice]').forEach(button => {
    const chosen = button.dataset.choice === id;
    button.classList.toggle('selected', chosen);
    button.setAttribute('aria-pressed', String(chosen));
  });
  const chosen = active.choices.find(choice => choice.id === id);
  const parsed = document.querySelector('#parsed');
  if (parsed && chosen) parsed.textContent = `已選擇 ${chosen.label}：${chosen.text}`;
  document.querySelector('#confirm').disabled = !parseText(false).reason;
}

function parseText(updateSelection = true) {
  const text = document.querySelector('#answer').value.trim();
  let token = null;
  let reason = '';
  let explicitChoice = false;
  let malformedExplicitChoice = false;
  if (text.startsWith('{')) {
    try {
      const value = JSON.parse(text);
      token = value.choice == null ? null : String(value.choice);
      reason = String(value.reasoning ?? value.reason ?? '').trim();
      explicitChoice = token != null;
      malformedExplicitChoice = token == null;
    } catch (_) { explicitChoice = true; malformedExplicitChoice = true; }
  }
  if (!explicitChoice) {
    const choicePrefix = /(?:選擇|选择|choice)["']?\s*[:：]/i.test(text);
    const choiceMatch = text.match(/(?:選擇|选择|choice)["']?\s*[:：]\s*([A-Z]+|\d+)/i);
    const reasonMatch = text.match(/(?:理由|reasoning|reason)\s*[:：]\s*([\s\S]+)/i);
    if (choicePrefix) {
      explicitChoice = true;
      if (choiceMatch) token = choiceMatch[1];
      else malformedExplicitChoice = true;
    }
    reason = reasonMatch ? reasonMatch[1].trim() : (explicitChoice ? '' : text);
  }
  if (updateSelection && explicitChoice) {
    const upper = token == null ? '' : String(token).toUpperCase();
    const index = /^\d+$/.test(upper) ? Number(upper) - 1 : upper.charCodeAt(0) - 65;
    const singleLetter = /^[A-Z]$/.test(upper);
    if (!malformedExplicitChoice && (singleLetter || /^\d+$/.test(upper)) && active.choices[index]) {
      selectedChoice = active.choices[index].id;
      document.querySelectorAll('[data-choice]').forEach(button => {
        const chosen = button.dataset.choice === selectedChoice;
        button.classList.toggle('selected', chosen);
        button.setAttribute('aria-pressed', String(chosen));
      });
      document.querySelector('#parsed').textContent = `已辨認 ${active.choices[index].label}：${active.choices[index].text}`;
    } else {
      selectedChoice = null;
      document.querySelectorAll('[data-choice]').forEach(button => { button.classList.remove('selected'); button.setAttribute('aria-pressed', 'false'); });
      document.querySelector('#parsed').textContent = '回答中的選項不存在，請重新確認';
    }
  }
  document.querySelector('#confirm').disabled = !selectedChoice || !reason;
  return {text, reason};
}

function parseAnswerSilent() { parseText(); }
function parseAnswer() {
  const {reason} = parseText();
  if (selectedChoice && reason) notify('已辨認選項與理由');
  else notify('請確認格式：選擇：A｜理由：……');
}

async function confirmChoice() {
  const {reason} = parseText();
  if (!selectedChoice || !reason) return notify('需要選項與理由才能保存');
  await perform('choose', {choice_id: selectedChoice, reason});
}

async function perform(action, extra = {}) {
  const buttons = [...document.querySelectorAll('button')];
  buttons.forEach(button => button.disabled = true);
  try {
    render(await api('/api/action', {method: 'POST', body: JSON.stringify({
      id: active.session.id,
      revision: active.session.revision,
      node_id: active.node_id,
      action,
      ...extra,
    })}));
  } catch (error) {
    notify(error.message);
    buttons.forEach(button => button.disabled = false);
  }
}

function bindCommon(data) {
  document.querySelector('#home').addEventListener('click', showHome);
  document.querySelector('#copy').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(data.copy_text);
      notify('情節已複製，可以貼給玩家了');
    } catch (_) {
      window.prompt('請複製以下情節給玩家：', data.copy_text);
    }
  });
  document.querySelector('#export').addEventListener('click', () => {
    window.location.href = `/api/export?id=${encodeURIComponent(data.session.id)}`;
  });
  document.querySelector('#backup').addEventListener('click', () => {
    window.location.href = `/api/backup?id=${encodeURIComponent(data.session.id)}`;
  });
}

function renderEnding(data) {
  const records = data.chapter_card.decisions.filter(item => item.selected_text).map((item, index) => `
    <div class="record"><div class="tiny">決策 ${index + 1}${item.random_event ? ` · 隨機事件：${escapeHtml(item.resolution)}` : ''}</div><p class="record-choice">${escapeHtml(item.selected_text)}</p><p class="record-reason">${escapeHtml(item.reason || '未填理由')}</p></div>`).join('');
  const endings = [...new Map(data.all_endings.map(item => [item.id, item])).values()].map(item => `
    <section class="ending-block"><h2 class="ending-title">${escapeHtml(item.title)}</h2><div class="ending-narrative">${escapeHtml(item.narrative)}</div></section>`).join('');
  app.innerHTML = frame(`
    <article class="panel ending">
      <div class="eyebrow">Chapter Complete</div>
      <h1>第 ${data.chapter.number} 章完成</h1>
      ${endings}
      <p class="lede">${escapeHtml(data.chapter_card.summary)}</p>
      <div class="record-list"><h2>本章記錄卡</h2>${records || '<p class="empty">本章沒有選擇節點。</p>'}</div>
      <div class="answer-box">
        <label for="reflection">玩家的章末回顧（由玩家親自寫，不由程式代替）</label>
        <textarea id="reflection" placeholder="貼上玩家對這一章的回顧……">${escapeHtml(data.chapter_card.reflection || '')}</textarea>
        <button id="save-reflection" class="button">保存章末回顧</button>
      </div>
      <div class="answer-actions"><button id="copy-card" class="button">複製章末記錄</button><button id="next" class="button primary">${data.has_next ? '進入下一章' : '完成這一周目'}</button><button id="export" class="button">匯出對話交接卡</button><button id="backup" class="button">匯出完整存檔</button><button id="home" class="button ghost">回存檔首頁</button></div>
    </article>`, 'CHAPTER SAVED');
  document.querySelector('#copy-card').addEventListener('click', async () => {
    const lines = [`第 ${data.chapter.number} 章・${data.chapter.title} 記錄卡`];
    data.chapter_card.decisions.filter(item => item.selected_text).forEach((item, index) => lines.push(`${index + 1}. ${item.selected_text}\n理由：${item.reason}`));
    lines.push('', `章末結果：${[...new Set(data.all_endings.map(item => item.title))].join('／')}`, '', '請寫下你對這一章的簡短回顧。');
    try { await navigator.clipboard.writeText(lines.join('\n')); notify('章末記錄已複製'); }
    catch (_) { window.prompt('請複製以下章末記錄給玩家：', lines.join('\n')); }
  });
  document.querySelector('#next').addEventListener('click', async () => {
    if (!(await saveReflectionIfNeeded(data))) return;
    await perform('next_chapter');
  });
  document.querySelector('#save-reflection').addEventListener('click', async () => {
    const reflection = document.querySelector('#reflection').value.trim();
    if (!reflection) return notify('貼上玩家的回顧後再保存');
    try {
      render(await api('/api/action', {method: 'POST', body: JSON.stringify({id: data.session.id, revision: data.session.revision, action: 'save_reflection', reflection})}));
      notify('章末回顧已保存');
    } catch (error) { notify(error.message); }
  });
  document.querySelector('#export').addEventListener('click', async () => {
    if (!(await saveReflectionIfNeeded(data))) return;
    window.location.href = `/api/export?id=${encodeURIComponent(data.session.id)}`;
  });
  document.querySelector('#backup').addEventListener('click', async () => {
    if (!(await saveReflectionIfNeeded(data))) return;
    window.location.href = `/api/backup?id=${encodeURIComponent(data.session.id)}`;
  });
  document.querySelector('#home').addEventListener('click', async () => {
    if (!(await saveReflectionIfNeeded(data))) return;
    await showHome();
  });
}

async function saveReflectionIfNeeded(data) {
  const field = document.querySelector('#reflection');
  if (!field) return true;
  const reflection = field.value.trim();
  if (reflection === (data.chapter_card.reflection || '')) return true;
  if (!reflection) {
    return window.confirm('章末回顧尚未保存。確定先離開嗎？');
  }
  try {
    const updated = await api('/api/action', {method: 'POST', body: JSON.stringify({id: data.session.id, revision: active.session.revision, action: 'save_reflection', reflection})});
    active = updated;
    data.session = updated.session;
    data.chapter_card = updated.chapter_card;
    return true;
  } catch (error) {
    notify(error.message);
    return false;
  }
}

function renderCampaignComplete(data) {
  const chapters = data.chapters.map(item => `<div class="record"><div class="tiny">第 ${item.chapter_number} 章</div><p class="record-choice">${escapeHtml(item.title)} · ${escapeHtml([...new Set(item.all_endings.map(ending => ending.title))].join('／'))}</p><p class="record-reason">${escapeHtml(item.summary)}</p></div>`).join('');
  const skipped = data.skipped_chapters.map(item => `<div class="record"><div class="tiny">第 ${item.chapter_number} 章 · 未進入</div><p class="record-choice">${escapeHtml(item.title)}</p><p class="record-reason">${escapeHtml(item.reason)}</p></div>`).join('');
  app.innerHTML = frame(`
    <article class="panel ending">
      <div class="eyebrow">Campaign Complete</div><h1>這條路，我走完了。</h1>
      <p class="lede">這條周目的所有實際章節、選擇與理由都已保存；依前序結果無法進入的章節會清楚標記。</p>
      <div class="record-list">${chapters}${skipped}</div>
      <div class="answer-actions"><button id="export" class="button primary">匯出完整記錄卡</button><button id="backup" class="button">匯出完整存檔</button><button id="home" class="button ghost">回存檔首頁</button></div>
    </article>`, 'RUN COMPLETE');
  document.querySelector('#export').addEventListener('click', () => window.location.href = `/api/export?id=${encodeURIComponent(data.session.id)}`);
  document.querySelector('#backup').addEventListener('click', () => window.location.href = `/api/backup?id=${encodeURIComponent(data.session.id)}`);
  document.querySelector('#home').addEventListener('click', showHome);
}

showHome().catch(error => { app.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`; });
