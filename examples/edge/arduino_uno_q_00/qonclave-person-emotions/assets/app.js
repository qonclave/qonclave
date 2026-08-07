/*
 * app.js — Client-side logic for Qonclave Person Emotions Dashboard
 */

const ui = new ArduinoUI();

const matrixBitmaps = {
  happy: [
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,1,0,0,0,0,0,0,1,0,0],
    [0,0,1,0,0,0,0,0,0,1,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [1,0,0,0,0,0,0,0,0,0,0,1],
    [0,1,0,0,0,0,0,0,0,0,1,0],
    [0,0,1,1,1,1,1,1,1,1,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0]
  ],
  sad: [
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,1,0,0,0,0,0,0,1,0,0],
    [0,0,1,0,0,0,0,0,0,1,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,1,1,1,1,1,1,1,1,0,0],
    [0,1,0,0,0,0,0,0,0,0,1,0],
    [1,0,0,0,0,0,0,0,0,0,0,1]
  ],
  surprise: [
    [0,0,1,1,0,0,0,0,1,1,0,0],
    [0,1,0,0,1,0,0,1,0,0,1,0],
    [0,1,1,1,1,0,0,1,1,1,1,0],
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,1,1,1,1,0,0,0,0],
    [0,0,0,1,0,0,0,0,1,0,0,0],
    [0,0,0,1,0,0,0,0,1,0,0,0],
    [0,0,0,0,1,1,1,1,0,0,0,0]
  ],
  angry: [
    [1,0,0,0,0,0,0,0,0,0,0,1],
    [0,1,1,0,0,0,0,0,0,1,1,0],
    [0,0,0,1,1,0,0,1,1,0,0,0],
    [0,0,1,1,0,0,0,0,1,1,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,1,1,1,1,1,1,1,1,0,0],
    [0,1,0,0,0,0,0,0,0,0,1,0],
    [0,0,0,0,0,0,0,0,0,0,0,0]
  ],
  neutral: [
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,1,1,0,0,0,0,1,1,0,0],
    [0,0,1,1,0,0,0,0,1,1,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [0,1,1,1,1,1,1,1,1,1,1,0],
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0]
  ],
  fear: [
    [0,0,0,1,1,0,0,1,1,0,0,0],
    [0,0,1,0,0,0,0,0,0,1,0,0],
    [0,1,1,1,0,0,0,0,1,1,1,0],
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [0,1,1,0,0,1,1,0,0,1,1,0],
    [0,0,0,1,1,0,0,1,1,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0]
  ],
  clear: [
    [0,0,0,0,0,0,0,0,0,0,0,1],
    [0,0,0,0,0,0,0,0,0,0,1,0],
    [0,0,0,0,0,0,0,0,0,1,0,0],
    [1,0,0,0,0,0,0,0,1,0,0,0],
    [0,1,0,0,0,0,0,1,0,0,0,0],
    [0,0,1,0,0,0,1,0,0,0,0,0],
    [0,0,0,1,0,1,0,0,0,0,0,0],
    [0,0,0,0,1,0,0,0,0,0,0,0]
  ]
};

const emoMeta = {
  happy: { label: "😃 HAPPY", color: "#3ecf8e", bg: "rgba(62, 207, 142, 0.15)", border: "rgba(62, 207, 142, 0.4)" },
  sad: { label: "😢 SAD", color: "#4a90e2", bg: "rgba(74, 144, 226, 0.15)", border: "rgba(74, 144, 226, 0.4)" },
  surprise: { label: "😲 SURPRISE", color: "#ffb700", bg: "rgba(255, 183, 0, 0.15)", border: "rgba(255, 183, 0, 0.4)" },
  angry: { label: "😠 ANGRY", color: "#ff3b30", bg: "rgba(255, 59, 48, 0.15)", border: "rgba(255, 59, 48, 0.4)" },
  neutral: { label: "😐 NEUTRAL", color: "#a0a8be", bg: "rgba(160, 168, 190, 0.15)", border: "rgba(160, 168, 190, 0.4)" },
  fear: { label: "😨 FEAR", color: "#d050c0", bg: "rgba(208, 80, 192, 0.15)", border: "rgba(208, 80, 192, 0.4)" },
  clear: { label: "✓ CLEAR (Safe)", color: "#008184", bg: "rgba(0, 129, 132, 0.15)", border: "rgba(0, 129, 132, 0.4)" }
};

function initVirtualMatrix() {
  const grid = document.getElementById('virtualMatrixGrid');
  if (!grid) return;
  grid.innerHTML = '';
  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 12; c++) {
      const dot = document.createElement('div');
      dot.id = `led_${r}_${c}`;
      dot.style.width = '22px';
      dot.style.height = '22px';
      dot.style.borderRadius = '3px';
      dot.style.background = '#131722';
      dot.style.transition = 'all 0.15s ease';
      grid.appendChild(dot);
    }
  }
  renderVirtualMatrix('clear');
}

function renderVirtualMatrix(emoName) {
  const bitmap = matrixBitmaps[emoName] || matrixBitmaps.clear;
  const meta = emoMeta[emoName] || emoMeta.clear;
  const badge = document.getElementById('activeEmotionBadge');
  
  if (badge) {
    badge.textContent = meta.label;
    badge.style.color = meta.color;
    badge.style.background = meta.bg;
    badge.style.borderColor = meta.border;
  }

  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 12; c++) {
      const dot = document.getElementById(`led_${r}_${c}`);
      if (dot) {
        const isOn = bitmap[r] && bitmap[r][c] === 1;
        dot.style.background = isOn ? '#ffb700' : '#131722';
        dot.style.boxShadow = isOn ? '0 0 10px #ffb700, 0 0 4px #ff8c00' : 'none';
      }
    }
  }
}

function updateEmotionBars(scores) {
  if (!scores) return;
  const emotions = ['happy', 'sad', 'surprise', 'angry', 'neutral', 'fear'];
  emotions.forEach(emo => {
    const bar = document.getElementById(`bar-${emo}`);
    if (bar) {
      const val = scores[emo] !== undefined ? Math.round(scores[emo] * 100) : 0;
      bar.style.width = `${val}%`;
      bar.textContent = `${val}%`;
    }
  });
}

function logEmotionEvent(emoName, source) {
  const list = document.getElementById('recentDetections');
  if (!list) return;
  
  const meta = emoMeta[emoName] || { label: emoName.toUpperCase(), color: '#fff' };
  const timeStr = new Date().toLocaleTimeString();
  
  const li = document.createElement('li');
  li.className = 'scan-container';
  li.innerHTML = `
    <span style="color: ${meta.color}; font-weight: 700;">${meta.label}</span>
    <span class="scan-content-time">${source} • ${timeStr}</span>
  `;
  
  list.insertBefore(li, list.firstChild);
  while (list.children.length > 10) {
    list.removeChild(list.lastChild);
  }
}

function testEmotion(emoName) {
  renderVirtualMatrix(emoName);
  logEmotionEvent(emoName, "Web Override");
  
  const fakeScores = { happy: 0, sad: 0, surprise: 0, angry: 0, neutral: 0, fear: 0 };
  if (fakeScores[emoName] !== undefined) fakeScores[emoName] = 1.0;
  updateEmotionBars(fakeScores);
  
  ui.send_message('test_emotion', emoName);
}

ui.on_message('emotion_update', async msg => {
  if (msg && msg.emotion) {
    renderVirtualMatrix(msg.emotion);
    if (msg.scores) updateEmotionBars(msg.scores);
    logEmotionEvent(msg.emotion, msg.source || "Camera");
  }
});

ui.on_message('knob_update', async msg => {
  if (msg && msg.threshold != null) {
    const input = document.getElementById('confidenceInput');
    const slider = document.getElementById('confidenceSlider');
    const display = document.getElementById('confidenceValueDisplay');
    if (input && slider && display) {
      input.value = msg.threshold.toFixed(2);
      slider.value = msg.threshold;
      display.textContent = msg.threshold.toFixed(2);
    }
  }
});

function initSlider() {
  const slider = document.getElementById('confidenceSlider');
  const input = document.getElementById('confidenceInput');
  const display = document.getElementById('confidenceValueDisplay');
  
  if (slider && input) {
    slider.addEventListener('input', () => {
      input.value = slider.value;
      if (display) display.textContent = slider.value;
      ui.send_message('override_th', slider.value);
    });
    input.addEventListener('change', () => {
      let val = parseFloat(input.value);
      if (isNaN(val)) val = 0.5;
      val = Math.max(0, Math.min(1, val));
      slider.value = val;
      input.value = val.toFixed(2);
      if (display) display.textContent = val.toFixed(2);
      ui.send_message('override_th', val.toString());
    });
  }
}

initVirtualMatrix();
initSlider();
