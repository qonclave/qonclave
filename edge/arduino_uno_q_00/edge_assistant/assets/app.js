const ui = new WebUI();
ui.on_connect(onConnected);
ui.on_disconnect(onDisconnected);
ui.on_message('state_change',  onStateChange);
ui.on_message('query_text',    onQueryText);
ui.on_message('response_text', onResponseText);
ui.on_message('hub_status',    onHubStatus);

const stateBadge  = document.getElementById('state-badge');
const stateHint   = document.getElementById('state-hint');
const hubStatus   = document.getElementById('hub-status');
const hubText     = document.getElementById('hub-status-text');
const queryBubble = document.getElementById('query-bubble');
const queryText   = document.getElementById('query-text');
const respBubble  = document.getElementById('response-bubble');
const respText    = document.getElementById('response-text');
const toolBadge   = document.getElementById('tool-badge');

const STATE_META = {
  idle:       { label: 'IDLE',       cls: 'state-idle',       hint: 'Say <strong>"Conclave"</strong> to start' },
  listening:  { label: 'LISTENING',  cls: 'state-listening',  hint: 'Recording your command…' },
  thinking:   { label: 'THINKING',   cls: 'state-thinking',   hint: 'Thinking…' },
  speaking:   { label: 'SPEAKING',   cls: 'state-speaking',   hint: 'Speaking…' },
};

function applyState(state) {
  const meta = STATE_META[state] || STATE_META.idle;
  stateBadge.textContent = meta.label;
  stateBadge.className = `state-badge ${meta.cls}`;
  stateHint.innerHTML = meta.hint;
}

function onConnected() {}
function onDisconnected() {}

function onStateChange(msg) {
  applyState(msg.state || 'idle');
}

function onQueryText(msg) {
  queryText.textContent = msg.text || '';
  queryBubble.classList.remove('hidden');
  // Reset response until new one arrives
  respBubble.classList.add('hidden');
  toolBadge.classList.add('hidden');
}

function onResponseText(msg) {
  respText.textContent = msg.text || '';
  respBubble.classList.remove('hidden');
  if (msg.tool_used) {
    toolBadge.textContent = `⚙ ${msg.tool_used}`;
    toolBadge.classList.remove('hidden');
  } else {
    toolBadge.classList.add('hidden');
  }
}

function onHubStatus(msg) {
  const online = !!msg.connected;
  hubStatus.classList.toggle('hub-online', online);
  hubStatus.classList.toggle('hub-offline', !online);
  hubText.textContent = online
    ? `Hub Connected: ${msg.host}:${msg.port}`
    : `Hub Offline: ${msg.host}:${msg.port}`;
}

// Initial state
applyState('idle');
