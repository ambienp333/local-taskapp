const API = '';
const SCREENS = ['screen-notes', 'screen-hotkeys', 'screen-journal', 'screen-flashcards'];
const COLOR_PRIORITY = ['fc', 'ur', 'co', 'ac', 'he', 'so', 'mi'];
const MODIFIER_RE = /^(fc|da|te\d*|ac|he|mi|so|co|ur)$/i;
const NARROW_BREAKPOINT = 900;

const state = {
    view: 'active',
    tasks: { active: [], daily: [] },
    selectedTask: null,
    editingTaskId: null,
    rightScreen: 0,
    prevRightScreen: null,
    shiftHoldActive: false,
};

let shiftHoldTimer = null;
let deselectTimer = null;
let dragSrcIndex = null;
let dragJustEnded = false;

// ---- Utils ----

function getColorClass(modifiers) {
    for (const mod of COLOR_PRIORITY) {
        if (modifiers.includes(mod)) return mod;
    }
    return 'mi';
}

function parseInput(raw) {
    const tokens = raw.trim().split(/\s+/);
    let nameEnd = tokens.length;
    for (let i = tokens.length - 1; i >= 0; i--) {
        if (MODIFIER_RE.test(tokens[i])) nameEnd = i;
        else break;
    }
    const modifiers = tokens.slice(nameEnd).map(m => m.toLowerCase());
    return {
        name: tokens.slice(0, nameEnd).join(' '),
        modifiers: modifiers.length
            ? (modifiers.some(m => m === 'da' || /^te/.test(m)) ? modifiers : ['da', ...modifiers])
            : ['da', 'mi'],
    };
}

function isNarrow() {
    return window.innerWidth < NARROW_BREAKPOINT;
}

function todayDateStr() {
    const d = new Date();
    return `${d.getMonth()+1}/${d.getDate()}/${String(d.getFullYear()).slice(2)}`;
}

function dateToSlug(dateStr) { return dateStr.replace(/\//g, '-'); }

function getSubtasks(notes) {
    if (!notes) return [];
    return notes.split('\n')
        .map((line, idx) => ({ line, idx }))
        .filter(({ line }) => /^- /.test(line))
        .map(({ line, idx }) => ({
            rawIdx: idx,
            text: line.replace(/^- (\[.\] )?/, ''),
            done: /^- \[x\] /i.test(line),
        }));
}

async function toggleSubtask(task, rawIdx) {
    const lines = (task.notes || '').split('\n');
    const line = lines[rawIdx];
    if (/^- \[x\] /i.test(line)) {
        lines[rawIdx] = line.replace(/^- \[x\] /i, '- [ ] ');
    } else if (/^- \[ \] /.test(line)) {
        lines[rawIdx] = line.replace(/^- \[ \] /, '- [x] ');
    } else {
        lines[rawIdx] = line.replace(/^- /, '- [x] ');
    }
    const notes = lines.join('\n');
    task.notes = notes;
    const t = state.tasks[state.view]?.find(t => t.id === task.id);
    if (t) t.notes = notes;
    const textarea = isNarrow()
        ? document.getElementById('notes-overlay-input')
        : document.getElementById('notes-area');
    if (textarea && state.selectedTask?.id === task.id) textarea.value = notes;
    render();
    await fetch(`${API}/api/tasks`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: task.id, notes }),
    });
}

// ---- API ----

async function syncFcTasks(data) {
    let fcRes;
    try { fcRes = await fetch(`${API}/api/flashcards/config`); }
    catch (e) { return; }
    if (!fcRes.ok) return;
    const subjects = await fcRes.json();

    const creates = [];
    for (const sub of subjects) {
        const fcTask = [...(data.active || []), ...(data.daily || [])].find(
            t => t.modifiers.includes('fc') && t.name.toLowerCase().includes(sub.subject.toLowerCase()));
        if (sub.enabled && !fcTask) {
            creates.push(sub);
        } else if (fcTask) {
            fcTask.name = `${sub.subject}[${sub.today_count}/${sub.daily_goal}]`;
        }
    }
    if (creates.length) {
        await Promise.all(creates.map(sub => fetch(`${API}/api/tasks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: `${sub.subject} flashcards`, modifiers: ['da', 'fc'] }),
        })));
        const r = await fetch(`${API}/api/tasks`);
        const fresh = await r.json();
        Object.assign(data, fresh);
        await syncFcTasks(data);
    }
}

async function loadTasks() {
    const res = await fetch(`${API}/api/tasks`);
    const data = await res.json();
    await syncFcTasks(data);
    state.tasks = data;
    // Re-sync selectedTask reference after reload
    if (state.selectedTask) {
        const list = [...(data.active || []), ...(data.daily || [])];
        state.selectedTask = list.find(t => t.id === state.selectedTask.id) || null;
        updateNotesPanel();
    }
    render();
    // Snapshot active tasks into today's journal (exclude fc tasks)
    const allTasks = [...(data.active || []), ...(data.daily || [])];
    fetch(`${API}/api/journal/${dateToSlug(todayDateStr())}/snapshot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tasks: allTasks.filter(t => !t.modifiers.includes('fc')).map(t => ({
            id: t.id, name: t.name, modifiers: t.modifiers, notes: t.notes || '',
        })) }),
    });
}

function completeTask(task) {
    if (task.modifiers.includes('fc')) return;
    const subtasks = getSubtasks(task.notes);
    if (subtasks.some(s => !s.done)) return;

    const list = state.tasks[state.view];
    const idx = list.findIndex(t => t.id === task.id);
    if (idx !== -1) list.splice(idx, 1);
    if (state.selectedTask?.id === task.id) {
        state.selectedTask = null;
        updateNotesPanel();
    }
    render();

    fetch(`${API}/api/tasks`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: task.id }),
    });
    fetch(`${API}/api/journal/${dateToSlug(todayDateStr())}/complete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: task.id, task: {
            id: task.id, name: task.name, modifiers: task.modifiers, notes: task.notes || '',
        } }),
    });
}

function completeFcTask(subject) {
    const all = [...(state.tasks.active || []), ...(state.tasks.daily || [])];
    const task = all.find(t => t.modifiers.includes('fc') && t.name.toLowerCase().includes(subject.toLowerCase()));
    if (!task) return;
    const view = state.tasks.active?.find(t => t.id === task.id) ? 'active' : 'daily';
    const list = state.tasks[view];
    const idx = list.findIndex(t => t.id === task.id);
    if (idx !== -1) list.splice(idx, 1);
    if (state.selectedTask?.id === task.id) { state.selectedTask = null; updateNotesPanel(); }
    render();
    fetch(`${API}/api/tasks`, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: task.id }) });
}

async function saveNotes() {
    if (!state.selectedTask) return;
    const textarea = isNarrow()
        ? document.getElementById('notes-overlay-input')
        : document.getElementById('notes-area');
    if (!textarea) return;
    const notes = textarea.value;
    if (notes === (state.selectedTask.notes || '')) return;
    state.selectedTask.notes = notes;
    const t = state.tasks[state.view]?.find(t => t.id === state.selectedTask.id);
    if (t) t.notes = notes;
    fetch(`${API}/api/tasks`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: state.selectedTask.id, notes }),
    });
}

// ---- Render ----

function render() {
    const inner = document.querySelector('.task-inner');
    inner.innerHTML = '';

    (state.tasks[state.view] || []).forEach((task, index) => {
        const div = document.createElement('div');
        div.className = `task ${getColorClass(task.modifiers)}`;
        div.dataset.id = task.id;
        div.textContent = task.name;
        div.draggable = true;

        if (state.selectedTask?.id === task.id) div.classList.add('selected');

        div.addEventListener('click', () => {
            if (dragJustEnded) return;
            if (task.modifiers.includes('fc')) {
                if (state.selectedTask) deselectTask();
                setRightScreen(SCREENS.indexOf('screen-flashcards'));
                initFlashcardScreen();
                return;
            }
            if (state.selectedTask?.id === task.id) {
                clearTimeout(deselectTimer);
                if (isNarrow()) {
                    showNotesOverlay();
                } else {
                    setRightScreen(0);
                    document.getElementById('notes-area').focus();
                }
            } else {
                selectTask(task);
            }
        });

        div.addEventListener('dblclick', () => {
            completeTask(task);
        });

        div.addEventListener('contextmenu', e => {
            e.preventDefault();
            if (state.selectedTask?.id === task.id) {
                deselectTask();
            } else {
                selectTask(task);
                if (isNarrow()) {
                    showNotesOverlay();
                } else {
                    setRightScreen(0);
                    document.getElementById('notes-area').focus();
                }
            }
        });

        div.addEventListener('dragstart', () => {
            dragSrcIndex = index;
            setTimeout(() => div.classList.add('dragging'), 0);
        });
        div.addEventListener('dragend', () => {
            div.classList.remove('dragging');
            dragJustEnded = true;
            setTimeout(() => { dragJustEnded = false; }, 50);
        });
        div.addEventListener('dragover', e => {
            e.preventDefault();
            div.classList.add('drag-over');
        });
        div.addEventListener('dragleave', () => div.classList.remove('drag-over'));
        div.addEventListener('drop', async e => {
            e.preventDefault();
            div.classList.remove('drag-over');
            if (dragSrcIndex === null || dragSrcIndex === index) return;
            const list = state.tasks[state.view];
            const [moved] = list.splice(dragSrcIndex, 1);
            list.splice(index, 0, moved);
            dragSrcIndex = null;
            render();
            await fetch(`${API}/api/tasks/reorder`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: list.map(t => t.id), list: state.view }),
            });
        });

        inner.appendChild(div);

        if (state.selectedTask?.id === task.id) {
            const subtasks = getSubtasks(task.notes);
            if (subtasks.length) {
                const stDiv = document.createElement('div');
                stDiv.className = 'subtasks';
                subtasks.forEach(({ text, done, rawIdx }) => {
                    const item = document.createElement('label');
                    item.className = `subtask-item${done ? ' done' : ''}`;
                    const cb = document.createElement('input');
                    cb.type = 'checkbox';
                    cb.checked = done;
                    cb.addEventListener('click', e => {
                        e.stopPropagation();
                        clearTimeout(deselectTimer);
                        toggleSubtask(task, rawIdx);
                    });
                    const span = document.createElement('span');
                    span.textContent = text;
                    item.appendChild(cb);
                    item.appendChild(span);
                    stDiv.appendChild(item);
                });
                div.appendChild(stDiv);
            }
        }
    });

    document.getElementById('view-label').textContent =
        state.view === 'active' ? '[Active]' : '[Daily]';
    document.body.classList.toggle('daily', state.view === 'daily');
}

function selectTask(task) {
    saveNotes();
    clearTimeout(deselectTimer);
    state.selectedTask = task;
    updateNotesPanel();
    render();
    if (isNarrow()) deselectTimer = setTimeout(deselectTask, 1000);
}

function deselectTask() {
    saveNotes();
    state.selectedTask = null;
    updateNotesPanel();
    render();
}

// ---- Right panel ----

function setRightScreen(index) {
    document.querySelectorAll('.panel-screen').forEach((el, i) => {
        el.classList.toggle('active', i === index);
    });
    state.rightScreen = index;
    if (SCREENS[index] === 'screen-journal')     initJournalScreen();
    if (SCREENS[index] === 'screen-flashcards') initFlashcardScreen();
}

function updateNotesPanel() {
    const textarea = document.getElementById('notes-area');
    textarea.disabled = !state.selectedTask;
    textarea.value = state.selectedTask ? (state.selectedTask.notes || '') : '';
    textarea.placeholder = state.selectedTask ? 'notes...' : 'select a task';
}

function buildHotkeysScreen() {
    const hotkeys = [
        ['Space',            'Toggle Active / Daily'],
        ['Click',            'Select task'],
        ['Click (selected)', 'Focus notes'],
        ['Right-click',      'Open / close note'],
        ['Tab',              'New task'],
        ['Enter (selected)', 'Focus notes'],
        ['Shift+Enter',      'Complete task'],
        ['Double-click',     'Complete task'],
        ['Drag',             'Reorder tasks'],
        ['← →',             'Cycle right panel'],
        ['↑ ↓',             'Cycle tasks'],
        ['Shift (hold)',     'Show this screen'],
        ['Escape',           'Deselect / cancel'],
    ];
    const modifiers = [
        ['da',  'daily',               ''],
        ['te#', 'temporary (# days)',  ''],
        ['ac',  'academic',            'ac'],
        ['he',  'health',              'he'],
        ['mi',  'misc',                'mi'],
        ['so',  'social',              'so'],
        ['co',  'coding',              'co'],
        ['ur',  'urgent',              'ur'],
    ];

    document.getElementById('screen-hotkeys').innerHTML = `
        <div class="hotkeys-content">
            <div class="hk-section">
                <div class="hk-title">Keyboard</div>
                ${hotkeys.map(([k, v]) => `
                    <div class="hk-row">
                        <span class="hk-key">${k}</span>
                        <span class="hk-val">${v}</span>
                    </div>`).join('')}
            </div>
            <div class="hk-section">
                <div class="hk-title">Modifiers</div>
                ${modifiers.map(([k, v, cls]) => `
                    <div class="hk-row">
                        <span class="hk-key ${cls}">${k}</span>
                        <span class="hk-val">${v}</span>
                    </div>`).join('')}
            </div>
        </div>`;
}

// ---- Input ----

function openInput(prefill = '', editingId = null) {
    clearTimeout(deselectTimer);
    const input = document.getElementById('task-input');
    document.getElementById('add-bar').classList.remove('hidden');
    state.editingTaskId = editingId;
    input.value = prefill;
    input.focus();
    if (prefill) input.setSelectionRange(prefill.length, prefill.length);
}

function closeInput() {
    document.getElementById('add-bar').classList.add('hidden');
    document.getElementById('task-input').value = '';
    state.editingTaskId = null;
}

async function submitTask() {
    const raw = document.getElementById('task-input').value.trim();
    const editingId = state.editingTaskId;
    closeInput();
    if (!raw) return;

    const { name, modifiers } = parseInput(raw);
    if (!name) return;

    if (editingId) {
        await fetch(`${API}/api/tasks`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: editingId, name, modifiers }),
        });
        if (state.selectedTask?.id === editingId) {
            state.selectedTask.name = name;
            state.selectedTask.modifiers = modifiers;
        }
    } else {
        await fetch(`${API}/api/tasks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, modifiers }),
        });
    }
    await loadTasks();
}

// ---- Narrow notes overlay ----

function showNotesOverlay() {
    if (!state.selectedTask) return;
    const textarea = document.getElementById('notes-overlay-input');
    textarea.value = state.selectedTask.notes || '';
    document.getElementById('notes-overlay').classList.remove('hidden');
    textarea.focus();
}

document.getElementById('save-notes-btn').addEventListener('click', async () => {
    const notes = document.getElementById('notes-overlay-input').value;
    if (state.selectedTask) {
        state.selectedTask.notes = notes;
        await fetch(`${API}/api/tasks`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: state.selectedTask.id, notes }),
        });
    }
    document.getElementById('notes-overlay').classList.add('hidden');
});

document.getElementById('close-notes-btn').addEventListener('click', () => {
    document.getElementById('notes-overlay').classList.add('hidden');
});

document.getElementById('notes-overlay-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.shiftKey) {
        e.preventDefault();
        const notes = document.getElementById('notes-overlay-input').value;
        if (state.selectedTask) {
            state.selectedTask.notes = notes;
            const t = state.tasks[state.view]?.find(t => t.id === state.selectedTask.id);
            if (t) t.notes = notes;
            fetch(`${API}/api/tasks`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: state.selectedTask.id, notes }),
            });
        }
        document.getElementById('notes-overlay').classList.add('hidden');
        deselectTask();
    }
});

document.getElementById('notes-area').addEventListener('focus', () => {
    clearTimeout(deselectTimer);
});

document.getElementById('notes-area').addEventListener('blur', () => {
    if (!isNarrow()) saveNotes();
});

document.getElementById('notes-area').addEventListener('input', () => {
    if (!state.selectedTask) return;
    const notes = document.getElementById('notes-area').value;
    state.selectedTask.notes = notes;
    const t = state.tasks[state.view]?.find(t => t.id === state.selectedTask.id);
    if (t) t.notes = notes;
    render();
});

document.getElementById('notes-area').addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    if (e.shiftKey) {
        e.preventDefault();
        document.getElementById('notes-area').blur();
        deselectTask();
        return;
    }
    const ta = e.target;
    const pos = ta.selectionStart;
    const currentLine = ta.value.substring(0, pos).split('\n').at(-1);
    if (/^- $/.test(currentLine)) {
        e.preventDefault();
        const lineStart = pos - currentLine.length;
        ta.value = ta.value.substring(0, lineStart) + ta.value.substring(pos);
        ta.selectionStart = ta.selectionEnd = lineStart;
    } else if (/^- /.test(currentLine)) {
        e.preventDefault();
        const insert = '\n- ';
        ta.value = ta.value.substring(0, pos) + insert + ta.value.substring(pos);
        ta.selectionStart = ta.selectionEnd = pos + insert.length;
    }
});

// ---- Clock ----

function updateClock() {
    const now = new Date();
    let h = now.getHours();
    const m = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    const ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    document.getElementById('clock').textContent =
        `${String(h).padStart(2, '0')}:${m}:${s} [${ampm}]`;
}

// ---- Keyboard ----

document.addEventListener('keydown', e => {
    const active = document.activeElement;
    if (active === document.getElementById('notes-area') ||
        active === document.getElementById('notes-overlay-input')) return;

    const inputOpen = !document.getElementById('add-bar').classList.contains('hidden');

    if (inputOpen) {
        if (e.key === 'Enter')  { e.preventDefault(); submitTask(); }
        if (e.key === 'Escape') { e.preventDefault(); closeInput(); }
        if (e.key === 'Tab')    { e.preventDefault(); closeInput(); }
        return;
    }

    // Flashcard rating keys (only when answer is revealed)
    if (fcState.mode === 'review' && fcState.revealed) {
        const ratingKeys = { '1':0,'2':1,'3':2,'4':3,'5':4,'6':5, 'z':0,'x':1,'c':2,'v':3,'b':4,'n':5 };
        if (e.key in ratingKeys) { e.preventDefault(); rateCard(ratingKeys[e.key]); return; }
    }

    // Shift hold → hotkeys (delayed to allow Shift+Enter chord)
    if (e.key === 'Shift' && !e.repeat) {
        shiftHoldTimer = setTimeout(() => {
            state.prevRightScreen = state.rightScreen;
            state.shiftHoldActive = true;
            setRightScreen(SCREENS.indexOf('screen-hotkeys'));
        }, 80);
    }

    // Shift+Enter → complete task
    if (e.key === 'Enter' && e.shiftKey) {
        e.preventDefault();
        clearTimeout(shiftHoldTimer);
        if (state.shiftHoldActive) {
            setRightScreen(state.prevRightScreen);
            state.prevRightScreen = null;
            state.shiftHoldActive = false;
        }
        if (state.selectedTask) completeTask(state.selectedTask);
        return;
    }

    // Arrow keys → right panel
    if (e.key === 'ArrowLeft') {
        e.preventDefault();
        if (fcState.mode === 'review') { prevCard(); return; }
        setRightScreen((state.rightScreen - 1 + SCREENS.length) % SCREENS.length);
        return;
    }
    if (e.key === 'ArrowRight') {
        e.preventDefault();
        if (fcState.mode === 'review') { nextCard(); return; }
        setRightScreen((state.rightScreen + 1) % SCREENS.length);
        return;
    }
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        e.preventDefault();
        document.body.classList.add('keyboard-nav');
        const list = state.tasks[state.view] || [];
        if (!list.length) return;
        const currentIdx = state.selectedTask
            ? list.findIndex(t => t.id === state.selectedTask.id)
            : -1;
        let nextIdx;
        if (e.key === 'ArrowUp') {
            nextIdx = currentIdx <= 0 ? list.length - 1 : currentIdx - 1;
        } else {
            nextIdx = currentIdx >= list.length - 1 ? 0 : currentIdx + 1;
        }
        selectTask(list[nextIdx]);
        return;
    }

    // Enter → focus notes
    if (e.key === 'Enter') {
        e.preventDefault();
        if (!state.selectedTask) return;
        if (isNarrow()) {
            showNotesOverlay();
        } else {
            setRightScreen(0);
            document.getElementById('notes-area').focus();
        }
        return;
    }

    // Space → show card / advance (review) or toggle view
    if (e.key === ' ') {
        e.preventDefault();
        if (fcState.mode === 'review') {
            if (!fcState.revealed) {
                revealCard();
                if (fcState.timedMode) startTimedPhase('answer');
            } else {
                fcState.cardIdx++;
                showCurrentCard();
            }
            return;
        }
        state.view = state.view === 'active' ? 'daily' : 'active';
        render();
        return;
    }

    // Tab → new task
    if (e.key === 'Tab') {
        e.preventDefault();
        openInput();
        return;
    }

    // Escape → deselect
    if (e.key === 'Escape') {
        e.preventDefault();
        deselectTask();
        return;
    }
});

document.addEventListener('keyup', e => {
    if (e.key === 'Shift') {
        clearTimeout(shiftHoldTimer);
        if (state.shiftHoldActive) {
            setRightScreen(state.prevRightScreen);
            state.prevRightScreen = null;
            state.shiftHoldActive = false;
        }
    }
});

document.addEventListener('mousemove', () => {
    document.body.classList.remove('keyboard-nav');
});

// ---- Journal ----

const journalState = {
    date:      null,
    calYear:   null,
    calMonth:  null,
    calOpen:   false,
    brightDays: [],
};

function initJournalScreen() {
    const screen = document.getElementById('screen-journal');
    if (screen.querySelector('#journal-date-input')) {
        if (journalState.date) loadJournalDate(journalState.date);
    } else {
        buildJournalScreen();
    }
}

function buildJournalScreen() {
    const screen = document.getElementById('screen-journal');
    screen.innerHTML = `
        <div class="journal-header">
            <input type="text" id="journal-date-input" autocomplete="off" spellcheck="false">
            <button id="journal-cal-btn">▼</button>
            <button id="journal-sync-btn" title="Sync with other devices">⟳</button>
            <button id="journal-resend-btn" title="Resend this day">↺</button>
            <span id="journal-sync-status"></span>
        </div>
        <div id="journal-calendar" class="hidden">
            <div class="cal-nav">
                <button id="cal-prev">←</button>
                <span id="cal-month-label"></span>
                <button id="cal-next">→</button>
            </div>
            <div id="cal-grid"></div>
        </div>
        <div id="journal-tasks"></div>
    `;

    const today = todayDateStr();
    journalState.date = today;
    document.getElementById('journal-date-input').value = today;

    document.getElementById('journal-date-input').addEventListener('change', e => {
        const val = e.target.value.trim();
        if (/^\d+\/\d+\/\d+$/.test(val)) loadJournalDate(val);
    });

    document.getElementById('journal-cal-btn').addEventListener('click', () => {
        journalState.calOpen = !journalState.calOpen;
        const cal = document.getElementById('journal-calendar');
        const btn = document.getElementById('journal-cal-btn');
        if (journalState.calOpen) {
            cal.classList.remove('hidden');
            btn.textContent = '▲';
            const now = new Date();
            loadCalendarMonth(now.getFullYear(), now.getMonth() + 1);
        } else {
            cal.classList.add('hidden');
            btn.textContent = '▼';
        }
    });

    document.getElementById('cal-prev').addEventListener('click', () => {
        let { calYear, calMonth } = journalState;
        calMonth--;
        if (calMonth < 1) { calMonth = 12; calYear--; }
        loadCalendarMonth(calYear, calMonth);
    });

    document.getElementById('cal-next').addEventListener('click', () => {
        let { calYear, calMonth } = journalState;
        calMonth++;
        if (calMonth > 12) { calMonth = 1; calYear++; }
        loadCalendarMonth(calYear, calMonth);
    });

    document.getElementById('journal-sync-btn').addEventListener('click', async () => {
        const btn    = document.getElementById('journal-sync-btn');
        const status = document.getElementById('journal-sync-status');
        btn.disabled = true;
        status.textContent = '...';
        status.className = 'sync-status-idle';
        try {
            const res  = await fetch(`${API}/api/sync/check`, { method: 'POST' });
            const data = await res.json();
            if (data.synced > 0) {
                status.textContent = `+${data.synced} synced`;
                status.className = 'sync-status-ok';
                await loadTasks();
            } else {
                status.textContent = 'up to date';
                status.className = 'sync-status-idle';
            }
        } catch (e) {
            status.textContent = 'error';
        }
        btn.disabled = false;
        setTimeout(() => { status.textContent = ''; }, 3000);
    });

    document.getElementById('journal-resend-btn').addEventListener('click', async () => {
        const btn    = document.getElementById('journal-resend-btn');
        const status = document.getElementById('journal-sync-status');
        btn.disabled = true;
        status.textContent = '...';
        try {
            const res  = await fetch(`${API}/api/sync/resend`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ date: journalState.date }),
            });
            const data = await res.json();
            status.textContent = data.sent > 0 ? `resent ${data.sent}` : 'nothing to resend';
            status.className = 'sync-status-idle';
        } catch (e) {
            status.textContent = 'error';
        }
        btn.disabled = false;
        setTimeout(() => { status.textContent = ''; }, 3000);
    });

    // Load pending count badge on open
    fetch(`${API}/api/sync/status`)
        .then(r => r.json())
        .then(d => {
            if (d.pending > 0) {
                const btn = document.getElementById('journal-sync-btn');
                if (btn) btn.textContent = `⟳ (${d.pending})`;
            }
        })
        .catch(() => {});

    loadJournalDate(today);
}

async function loadJournalDate(dateStr) {
    journalState.date = dateStr;
    const input = document.getElementById('journal-date-input');
    if (input) input.value = dateStr;
    const res = await fetch(`${API}/api/journal/${dateToSlug(dateStr)}`);
    const data = await res.json();
    renderJournalTasks(data.tasks || []);
    if (journalState.calOpen) renderCalendar();
}

function renderJournalTasks(tasks) {
    const container = document.getElementById('journal-tasks');
    if (!container) return;
    container.innerHTML = '';
    if (!tasks.length) {
        const empty = document.createElement('div');
        empty.className = 'journal-empty';
        empty.textContent = 'no activity';
        container.appendChild(empty);
        return;
    }
    tasks.forEach(task => {
        const div = document.createElement('div');
        div.className = `journal-task ${getColorClass(task.modifiers || [])}${task.completed ? ' completed' : ''}`;

        const header = document.createElement('div');
        header.className = 'journal-task-header';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'journal-task-name';
        nameSpan.textContent = (task.completed ? '✓ ' : '') + task.name;
        header.appendChild(nameSpan);

        if (task.completed) {
            const btn = document.createElement('button');
            btn.className = 'journal-reinstate-btn';
            btn.textContent = '↩';
            btn.title = 'Reinstate to active';
            btn.addEventListener('click', async () => {
                btn.disabled = true;
                await fetch(`${API}/api/tasks/reinstate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ task: {
                        id: task.id, name: task.name,
                        modifiers: task.modifiers || [], notes: task.notes || '',
                    }}),
                });
                await loadTasks();
            });
            header.appendChild(btn);
        }

        div.appendChild(header);

        if (task.notes && task.notes.trim()) {
            const notes = document.createElement('div');
            notes.className = 'journal-task-notes';
            notes.textContent = task.notes;
            div.appendChild(notes);
        }
        container.appendChild(div);
    });
}

async function loadCalendarMonth(year, month) {
    journalState.calYear  = year;
    journalState.calMonth = month;
    const res = await fetch(`${API}/api/journal/calendar/${year}/${month}`);
    const data = await res.json();
    journalState.brightDays = data.bright_days || [];
    renderCalendar();
}

function renderCalendar() {
    const { calYear, calMonth, brightDays, date } = journalState;
    if (!calYear) return;

    document.getElementById('cal-month-label').textContent =
        `${new Date(calYear, calMonth - 1).toLocaleString('default', { month: 'long' })} ${calYear}`;

    const grid = document.getElementById('cal-grid');
    grid.innerHTML = '';
    grid.className = 'cal-grid';

    ['Su','Mo','Tu','We','Th','Fr','Sa'].forEach(d => {
        const cell = document.createElement('div');
        cell.className = 'cal-header';
        cell.textContent = d;
        grid.appendChild(cell);
    });

    const firstDay    = new Date(calYear, calMonth - 1, 1).getDay();
    const daysInMonth = new Date(calYear, calMonth, 0).getDate();
    const now         = new Date();
    const todayStr    = todayDateStr();

    for (let i = 0; i < firstDay; i++) {
        const cell = document.createElement('div');
        cell.className = 'cal-cell';
        grid.appendChild(cell);
    }

    for (let d = 1; d <= daysInMonth; d++) {
        const dayStr = `${calMonth}/${d}/${String(calYear).slice(2)}`;
        const cell   = document.createElement('div');
        cell.className = 'cal-cell cal-day';
        if (brightDays.includes(d)) cell.classList.add('bright');
        if (dayStr === todayStr)    cell.classList.add('today');
        if (dayStr === date)        cell.classList.add('selected');
        cell.textContent = d;
        cell.addEventListener('click', () => {
            journalState.calOpen = false;
            document.getElementById('journal-calendar').classList.add('hidden');
            document.getElementById('journal-cal-btn').textContent = '▼';
            loadJournalDate(dayStr);
        });
        grid.appendChild(cell);
    }
}

// ---- Flashcards ----

const TIMED_DURATION = 4000;

const fcState = {
    mode:       'config',
    subject:    null,
    allCards:   [],
    dueCards:   [],
    cards:      [],
    cardIdx:    0,
    revealed:   false,
    filter:     'due',
    isRandom:   true,
    timedMode:  false,
    timedTimer: null,
    timedPhase: null,
    stats:      { due: 0, seen: 0, total: 0 },
};

const FC_RATINGS = ['blackout', 'wrong', 'almost', 'correct', 'easy', 'perfect'];

function shuffle(arr) {
    const a = [...arr];
    for (let i = a.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
}

function initFlashcardScreen() {
    const screen = document.getElementById('screen-flashcards');
    if (screen.querySelector('#fc-config')) {
        if (fcState.mode === 'config') loadFcConfig();
        return;
    }
    buildFlashcardScreen();
}

function buildFlashcardScreen() {
    const screen = document.getElementById('screen-flashcards');
    screen.innerHTML = `
        <div id="fc-config"></div>
        <div id="fc-review" class="hidden">
            <div id="fc-review-header">
                <div id="fc-stats"></div>
                <button id="fc-exit-review">← back</button>
            </div>
            <div id="fc-filters"></div>
            <div id="fc-card-wrap"></div>
            <div id="fc-rating-btns" class="hidden">
                ${FC_RATINGS.map((r, i) => `<button class="fc-rate-btn" data-rating="${i}">${r}</button>`).join('')}
            </div>
            <div id="fc-progress"></div>
        </div>
    `;

    document.getElementById('fc-exit-review').addEventListener('click', exitFcReview);
    document.querySelectorAll('.fc-rate-btn').forEach(btn => {
        btn.addEventListener('click', () => rateCard(parseInt(btn.dataset.rating)));
    });

    loadFcConfig();
}

async function loadFcConfig() {
    const container = document.getElementById('fc-config');
    try {
        const res = await fetch(`${API}/api/flashcards/config`);
        if (!res.ok) {
            const err = await res.text();
            if (container) container.innerHTML = `<div class="fc-error">server error ${res.status}: ${err}</div>`;
            return;
        }
        const subjects = await res.json();
        renderFcConfig(subjects);
    } catch (e) {
        if (container) container.innerHTML = `<div class="fc-error">connection error: ${e.message}</div>`;
    }
}

function renderFcConfig(subjects) {
    const container = document.getElementById('fc-config');
    if (!container) return;
    container.innerHTML = '';

    subjects.forEach(sub => {
        const div = document.createElement('div');
        div.className = 'fc-subject';

        // Header: [enabled toggle] [name] [today / goal input] [review btn]
        const header = document.createElement('div');
        header.className = 'fc-subject-header';

        const enabledCb = document.createElement('input');
        enabledCb.type    = 'checkbox';
        enabledCb.checked = sub.enabled;
        enabledCb.title   = 'enable subject';
        enabledCb.addEventListener('change', async () => {
            fetch(`${API}/api/flashcards/config`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ subject: sub.subject, enabled: enabledCb.checked, daily_goal: sub.daily_goal }),
            });
            if (enabledCb.checked) {
                const existing = [...(state.tasks.active || []), ...(state.tasks.daily || [])].find(
                    t => t.modifiers.includes('fc') && t.name.toLowerCase().includes(sub.subject.toLowerCase()));
                if (!existing) {
                    await fetch(`${API}/api/tasks`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name: `${sub.subject} flashcards`, modifiers: ['da', 'fc'] }),
                    });
                    loadTasks();
                }
            } else {
                const task = [...(state.tasks.active || []), ...(state.tasks.daily || [])].find(
                    t => t.modifiers.includes('fc') && t.name.toLowerCase().includes(sub.subject.toLowerCase()));
                if (task) {
                    await fetch(`${API}/api/tasks`, {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ id: task.id }),
                    });
                    loadTasks();
                }
            }
        });

        const nameSpan = document.createElement('span');
        nameSpan.className = 'fc-subject-name';
        nameSpan.textContent = sub.subject;

        const countWrap = document.createElement('span');
        countWrap.className = 'fc-subject-count';
        countWrap.textContent = `${sub.today_count} / `;

        const goalInput = document.createElement('input');
        goalInput.type      = 'number';
        goalInput.className = 'fc-goal-input';
        goalInput.value     = sub.daily_goal;
        goalInput.min       = 1;
        const saveGoal = () => {
            const val = parseInt(goalInput.value) || 30;
            fetch(`${API}/api/flashcards/config`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ subject: sub.subject, enabled: sub.enabled, daily_goal: val }),
            });
        };
        goalInput.addEventListener('blur', saveGoal);
        goalInput.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); goalInput.blur(); } });
        countWrap.appendChild(goalInput);

        const reviewBtn = document.createElement('button');
        reviewBtn.className = 'fc-review-btn';
        reviewBtn.textContent = 'review';
        reviewBtn.dataset.subject = sub.subject;
        reviewBtn.addEventListener('click', () => startFcReview(sub.subject, sub.today_count, sub.daily_goal));

        header.appendChild(enabledCb);
        header.appendChild(nameSpan);
        header.appendChild(countWrap);
        header.appendChild(reviewBtn);
        div.appendChild(header);

        // File list
        if (sub.files.length) {
            const fileList = document.createElement('div');
            fileList.className = 'fc-file-list';
            sub.files.forEach(f => {
                const row = document.createElement('div');
                row.className = 'fc-file-row';

                const lbl = document.createElement('label');
                lbl.className = 'fc-file-label';
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.checked = f.enabled;
                cb.addEventListener('change', () => {
                    fetch(`${API}/api/flashcards/config`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ subject: sub.subject, filename: f.filename, file_enabled: cb.checked }),
                    });
                });
                lbl.appendChild(cb);
                lbl.appendChild(document.createTextNode(' ' + f.filename));

                const editBtn = document.createElement('button');
                editBtn.className = 'fc-file-edit';
                editBtn.textContent = 'edit';

                const del = document.createElement('button');
                del.className = 'fc-file-del';
                del.textContent = '×';
                del.addEventListener('click', async () => {
                    await fetch(`${API}/api/flashcards/config`, {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ subject: sub.subject, filename: f.filename }),
                    });
                    loadFcConfig();
                });

                row.appendChild(lbl);
                row.appendChild(editBtn);
                row.appendChild(del);
                fileList.appendChild(row);

                // Inline edit area (hidden until edit clicked)
                const editArea = document.createElement('div');
                editArea.className = 'fc-edit-area hidden';

                const ta = document.createElement('textarea');
                ta.className = 'fc-edit-textarea';
                ta.spellcheck = false;

                const editBtns = document.createElement('div');
                editBtns.className = 'fc-edit-btns';

                const saveBtn = document.createElement('button');
                saveBtn.textContent = 'save';
                saveBtn.addEventListener('click', async () => {
                    await fetch(`${API}/api/flashcards/upload`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ subject: sub.subject, filename: f.filename, content: ta.value }),
                    });
                    editArea.classList.add('hidden');
                });

                const cancelBtn = document.createElement('button');
                cancelBtn.textContent = 'cancel';
                cancelBtn.addEventListener('click', () => editArea.classList.add('hidden'));

                editBtns.appendChild(saveBtn);
                editBtns.appendChild(cancelBtn);
                editArea.appendChild(ta);
                editArea.appendChild(editBtns);
                fileList.appendChild(editArea);

                editBtn.addEventListener('click', async () => {
                    if (!editArea.classList.contains('hidden')) {
                        editArea.classList.add('hidden');
                        return;
                    }
                    const res = await fetch(`${API}/api/flashcards/file?subject=${sub.subject}&filename=${encodeURIComponent(f.filename)}`);
                    const data = await res.json();
                    ta.value = data.content || '';
                    editArea.classList.remove('hidden');
                    ta.focus();
                });
            });
            div.appendChild(fileList);
        }

        // Upload
        const uploadWrap = document.createElement('div');
        uploadWrap.className = 'fc-upload';

        const fileInput = document.createElement('input');
        fileInput.type      = 'file';
        fileInput.accept    = '.md';
        fileInput.className = 'fc-file-input hidden';
        fileInput.addEventListener('change', async () => {
            const file = fileInput.files[0];
            if (!file) return;
            const content = await file.text();
            await fetch(`${API}/api/flashcards/upload`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ subject: sub.subject, filename: file.name, content }),
            });
            fileInput.value = '';
            loadFcConfig();
        });

        const uploadBtn = document.createElement('button');
        uploadBtn.className   = 'fc-upload-btn';
        uploadBtn.textContent = '+ upload .md';
        uploadBtn.addEventListener('click', () => fileInput.click());

        uploadWrap.appendChild(fileInput);
        uploadWrap.appendChild(uploadBtn);
        div.appendChild(uploadWrap);
        container.appendChild(div);
    });
}

async function startFcReview(subject, todayCount = 0, dailyGoal = 30) {
    const res  = await fetch(`${API}/api/flashcards/cards?subject=${subject}`);
    const data = await res.json();

    if (!data.all || !data.all.length) {
        alert('No cards for ' + subject);
        return;
    }

    fcState.mode     = 'review';
    fcState.subject  = subject;
    fcState.allCards = data.all;
    fcState.dueCards = data.due;
    fcState.stats    = data.stats;
    fcState.filter     = 'due';
    fcState.isRandom   = true;
    fcState.revealed   = false;
    fcState.todayCount = todayCount;
    fcState.dailyGoal  = dailyGoal;

    document.getElementById('fc-config').classList.add('hidden');
    document.getElementById('fc-review').classList.remove('hidden');

    const s = data.stats;
    document.getElementById('fc-stats').textContent =
        `due: ${s.due}  seen: ${s.seen}  total: ${s.total}`;

    buildFcFilters();
    applyFcFilter('due');
}

function buildFcFilters() {
    const files   = [...new Set(fcState.allCards.map(c => c.filename))];
    const filters = [
        { key: 'due', label: 'due' },
        { key: 'all', label: 'all' },
        ...files.map(f => ({ key: f, label: f.replace(/\.md$/i, '') })),
    ];

    const row = document.getElementById('fc-filters');
    row.innerHTML = '';

    filters.forEach(({ key, label }) => {
        const btn = document.createElement('button');
        btn.className = 'fc-filter-btn' + (key === fcState.filter ? ' active' : '');
        btn.textContent = label;
        btn.addEventListener('click', () => applyFcFilter(key));
        row.appendChild(btn);
    });

    const randBtn = document.createElement('button');
    randBtn.className = 'fc-filter-btn' + (fcState.isRandom ? ' active' : '');
    randBtn.id = 'fc-random-btn';
    randBtn.textContent = 'random';
    randBtn.addEventListener('click', () => {
        fcState.isRandom = !fcState.isRandom;
        randBtn.classList.toggle('active', fcState.isRandom);
        applyFcFilter(fcState.filter);
    });
    row.appendChild(randBtn);

    const timedBtn = document.createElement('button');
    timedBtn.className = 'fc-filter-btn' + (fcState.timedMode ? ' active' : '');
    timedBtn.id = 'fc-timed-btn';
    timedBtn.textContent = 'timed';
    timedBtn.addEventListener('click', () => {
        fcState.timedMode = !fcState.timedMode;
        timedBtn.classList.toggle('active', fcState.timedMode);
        if (fcState.timedMode) {
            startTimedPhase('question');
        } else {
            stopTimedMode();
            const bar = document.getElementById('fc-timer-bar');
            if (bar) bar.parentElement.classList.add('hidden');
        }
    });
    row.appendChild(timedBtn);
}

function applyFcFilter(key) {
    stopTimedMode();
    fcState.filter = key;
    document.querySelectorAll('.fc-filter-btn:not(#fc-random-btn)').forEach(b => {
        const label = key === 'due' ? 'due' : key === 'all' ? 'all' : key.replace(/\.md$/i, '');
        b.classList.toggle('active', b.textContent === label);
    });

    let cards;
    if (key === 'due')      cards = [...fcState.dueCards];
    else if (key === 'all') cards = [...fcState.allCards];
    else                    cards = fcState.allCards.filter(c => c.filename === key);

    if (fcState.isRandom) cards = shuffle(cards);
    fcState.cards   = cards;
    fcState.cardIdx = 0;
    showCurrentCard();
}

function renderMath(el) {
    if (typeof renderMathInElement !== 'undefined') {
        renderMathInElement(el, {
            delimiters: [
                { left: '$$', right: '$$', display: true },
                { left: '$',  right: '$',  display: false },
            ],
            throwOnError: false,
        });
    }
}

function showCurrentCard() {
    const wrap = document.getElementById('fc-card-wrap');
    document.getElementById('fc-rating-btns').classList.add('hidden');

    if (!fcState.cards.length) {
        wrap.innerHTML = '<div class="fc-empty">no cards in this view</div>';
        document.getElementById('fc-progress').textContent = '';
        return;
    }
    if (fcState.cardIdx >= fcState.cards.length) {
        wrap.innerHTML = '<div class="fc-empty">session complete</div>';
        document.getElementById('fc-progress').textContent = '';
        return;
    }

    const card = fcState.cards[fcState.cardIdx];
    fcState.revealed = false;
    wrap.innerHTML = '';

    const meta = document.createElement('div');
    meta.id = 'fc-card-meta';
    const fileTag = document.createElement('span');
    fileTag.className = 'fc-tag';
    fileTag.textContent = card.filename.replace(/\.md$/i, '');
    meta.appendChild(fileTag);
    if (card.reps > 0) {
        const repsTag = document.createElement('span');
        repsTag.className = 'fc-tag';
        repsTag.textContent = `×${card.reps}`;
        meta.appendChild(repsTag);
    }

    const frontEl = document.createElement('div');
    frontEl.id = 'fc-front';
    frontEl.textContent = card.front;

    const backEl = document.createElement('div');
    backEl.id = 'fc-back';
    backEl.className = 'hidden';
    backEl.textContent = card.back;

    const showBtn = document.createElement('button');
    showBtn.id = 'fc-show-btn';
    showBtn.textContent = 'show answer';
    showBtn.addEventListener('click', revealCard);

    const timerWrap = document.createElement('div');
    timerWrap.id = 'fc-timer-wrap';
    timerWrap.className = fcState.timedMode ? '' : 'hidden';
    const timerBar = document.createElement('div');
    timerBar.id = 'fc-timer-bar';
    timerWrap.appendChild(timerBar);

    wrap.appendChild(meta);
    wrap.appendChild(frontEl);
    wrap.appendChild(backEl);
    wrap.appendChild(showBtn);
    wrap.appendChild(timerWrap);

    renderMath(frontEl);
    renderMath(backEl);

    document.getElementById('fc-progress').textContent =
        `${fcState.cardIdx + 1} / ${fcState.cards.length}`;

    if (fcState.timedMode) startTimedPhase('question');
}

function revealCard() {
    if (fcState.revealed) return;
    fcState.revealed = true;
    document.getElementById('fc-back').classList.remove('hidden');
    document.getElementById('fc-show-btn').classList.add('hidden');
    document.getElementById('fc-rating-btns').classList.remove('hidden');
}

function rateCard(rating) {
    const card = fcState.cards[fcState.cardIdx];

    if (card.seen === 0) { card.seen = 1; fcState.stats.seen++; }
    if (card.is_due)     { card.is_due = false; fcState.stats.due = Math.max(0, fcState.stats.due - 1); }

    fcState.todayCount++;
    if (fcState.todayCount >= fcState.dailyGoal) completeFcTask(fcState.subject);

    const statsEl = document.getElementById('fc-stats');
    if (statsEl) {
        const s = fcState.stats;
        statsEl.textContent = `due: ${s.due}  seen: ${s.seen}  total: ${s.total}`;
    }

    fetch(`${API}/api/flashcards/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ card_id: card.id, subject: card.subject, rating }),
    });

    fcState.cardIdx++;
    showCurrentCard();
}

function prevCard() {
    if (fcState.cardIdx > 0) { fcState.cardIdx--; showCurrentCard(); }
}

function nextCard() {
    if (fcState.cardIdx < fcState.cards.length) { fcState.cardIdx++; showCurrentCard(); }
}

function startTimedPhase(phase) {
    clearTimeout(fcState.timedTimer);
    fcState.timedPhase = phase;

    const bar = document.getElementById('fc-timer-bar');
    if (bar) {
        bar.style.transition = 'none';
        bar.style.width = '100%';
        requestAnimationFrame(() => requestAnimationFrame(() => {
            bar.style.transition = `width ${TIMED_DURATION}ms linear`;
            bar.style.width = '0%';
        }));
    }

    fcState.timedTimer = setTimeout(() => {
        if (phase === 'question') {
            revealCard();
            startTimedPhase('answer');
        } else {
            fcState.cardIdx++;
            showCurrentCard();
        }
    }, TIMED_DURATION);
}

function stopTimedMode() {
    clearTimeout(fcState.timedTimer);
    fcState.timedTimer = null;
    fcState.timedPhase = null;
}

function exitFcReview() {
    stopTimedMode();
    fcState.timedMode = false;
    fcState.mode     = 'config';
    fcState.subject  = null;
    fcState.allCards = [];
    fcState.dueCards = [];
    fcState.cards    = [];
    fcState.cardIdx  = 0;
    fcState.revealed = false;
    document.getElementById('fc-review').classList.add('hidden');
    document.getElementById('fc-config').classList.remove('hidden');
    loadFcConfig();
}

// ---- Init ----
setInterval(updateClock, 1000);
updateClock();
buildHotkeysScreen();
loadTasks();
