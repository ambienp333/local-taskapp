const API = '';
const SCREENS = ['screen-notes', 'screen-hotkeys'];
const COLOR_PRIORITY = ['ur', 'co', 'ac', 'he', 'so', 'mi'];
const MODIFIER_RE = /^(da|te\d+|ac|he|mi|so|co|ur)$/i;
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
const tapState = {};
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
        modifiers: modifiers.length ? modifiers : ['da', 'mi'],
    };
}

function isNarrow() {
    return window.innerWidth < NARROW_BREAKPOINT;
}

// ---- API ----

async function loadTasks() {
    const res = await fetch(`${API}/api/tasks`);
    const data = await res.json();
    state.tasks = data;
    // Re-sync selectedTask reference after reload
    if (state.selectedTask) {
        const list = [...(data.active || []), ...(data.daily || [])];
        state.selectedTask = list.find(t => t.id === state.selectedTask.id) || null;
        updateNotesPanel();
    }
    render();
}

async function completeTask(task) {
    const list = state.tasks[state.view];
    const idx = list.findIndex(t => t.id === task.id);
    if (idx !== -1) list.splice(idx, 1);
    if (state.selectedTask?.id === task.id) {
        state.selectedTask = null;
        updateNotesPanel();
    }
    render();
    await fetch(`${API}/api/tasks`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: task.id }),
    });
    await loadTasks();
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
            handleTaskClick(task);
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
    });

    document.getElementById('view-label').textContent =
        state.view === 'active' ? '[Active]' : '[Daily]';
    document.body.classList.toggle('daily', state.view === 'daily');
}

function handleTaskClick(task) {
    if (!tapState[task.id]) tapState[task.id] = { count: 0, timer: null };
    tapState[task.id].count++;
    clearTimeout(tapState[task.id].timer);

    if (tapState[task.id].count === 1) {
        tapState[task.id].timer = setTimeout(() => { tapState[task.id].count = 0; }, 400);
        selectTask(task);
    } else {
        tapState[task.id].count = 0;
        completeTask(task);
    }
}

function selectTask(task) {
    saveNotes();
    state.selectedTask = task;
    document.querySelectorAll('.task').forEach(el => {
        el.classList.toggle('selected', el.dataset.id === task.id);
    });
    updateNotesPanel();
}

function deselectTask() {
    saveNotes();
    state.selectedTask = null;
    document.querySelectorAll('.task.selected').forEach(el => el.classList.remove('selected'));
    updateNotesPanel();
}

// ---- Right panel ----

function setRightScreen(index) {
    document.querySelectorAll('.panel-screen').forEach((el, i) => {
        el.classList.toggle('active', i === index);
    });
    state.rightScreen = index;
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
        ['Space (selected)', 'Edit task'],
        ['Tab',              'New task'],
        ['Enter (selected)', 'Focus notes'],
        ['Shift+Enter',      'Complete task'],
        ['Double-click',     'Complete task'],
        ['Drag',             'Reorder tasks'],
        ['← →',             'Cycle right panel'],
        ['↑ ↓',             'Scroll right panel'],
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

document.getElementById('notes-area').addEventListener('blur', () => {
    if (!isNarrow()) saveNotes();
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
    const inputOpen = !document.getElementById('add-bar').classList.contains('hidden');

    if (inputOpen) {
        if (e.key === 'Enter')  { e.preventDefault(); submitTask(); }
        if (e.key === 'Escape') { e.preventDefault(); closeInput(); }
        if (e.key === 'Tab')    { e.preventDefault(); closeInput(); }
        return;
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
        setRightScreen((state.rightScreen - 1 + SCREENS.length) % SCREENS.length);
        return;
    }
    if (e.key === 'ArrowRight') {
        e.preventDefault();
        setRightScreen((state.rightScreen + 1) % SCREENS.length);
        return;
    }
    if (e.key === 'ArrowUp') {
        e.preventDefault();
        document.querySelector('.panel-screen.active')?.scrollBy(0, -60);
        return;
    }
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        document.querySelector('.panel-screen.active')?.scrollBy(0, 60);
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

    // Space → edit task (selected) or toggle view
    if (e.key === ' ') {
        e.preventDefault();
        if (state.selectedTask) {
            const prefill = [state.selectedTask.name, ...state.selectedTask.modifiers].join(' ');
            openInput(prefill, state.selectedTask.id);
        } else {
            state.view = state.view === 'active' ? 'daily' : 'active';
            render();
        }
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

// ---- Init ----
setInterval(updateClock, 1000);
updateClock();
buildHotkeysScreen();
loadTasks();
