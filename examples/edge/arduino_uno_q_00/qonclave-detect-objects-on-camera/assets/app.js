// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

let errorContainer = document.getElementById('error-container');
const recentDetectionsElement = document.getElementById('recentDetections');
const feedbackContentElement = document.getElementById('feedback-content');
const MAX_RECENT_SCANS = 5;
let scans = [];

const ui = new WebUI();
ui.on_connect(onUIConnected);
ui.on_disconnect(onUIDisconnected);
ui.on_message('detection', async message => {
  printDetection(message);
  renderDetections();
  updateFeedback(message);
});

let matrixBitmaps = {
  clear: [
    [0,0,0,0,0,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,1,0,0],
    [0,0,0,0,0,0,0,0,1,0,0,0],
    [0,0,0,0,0,0,0,1,0,0,0,0],
    [0,1,0,0,0,0,1,0,0,0,0,0],
    [0,0,1,0,0,1,0,0,0,0,0,0],
    [0,0,0,1,1,0,0,0,0,0,0,0],
    [0,0,0,0,0,0,0,0,0,0,0,0]
  ]
};

function initVirtualMatrix() {
  const grid = document.getElementById('virtualMatrixGrid');
  if (!grid) return;
  grid.innerHTML = '';
  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 12; c++) {
      const dot = document.createElement('div');
      dot.id = `led_${r}_${c}`;
      dot.style.width = '14px';
      dot.style.height = '14px';
      dot.style.borderRadius = '2px';
      dot.style.background = '#1a1e26';
      dot.style.transition = 'all 0.15s';
      grid.appendChild(dot);
    }
  }
  renderVirtualMatrix('clear');
}

function renderVirtualMatrix(iconName, customBitmap = null, isAiGenerated = false) {
  const rawEntry = customBitmap || matrixBitmaps[iconName] || matrixBitmaps.clear;
  const bitmap = (rawEntry && rawEntry.bitmap) ? rawEntry.bitmap : rawEntry;
  const text = document.getElementById('ledArrayText');
  if (text) {
    if (iconName && iconName !== 'clear') {
      if (isAiGenerated) {
        text.innerHTML = `${iconName.toUpperCase()} <span style="background: linear-gradient(135deg, #a855f7, #ec4899); color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75em; margin-left: 6px; box-shadow: 0 0 8px rgba(168, 85, 247, 0.5);">✨ AI ICON</span>`;
        text.style.color = '#e879f9';
      } else {
        text.textContent = `${iconName.toUpperCase()} DETECTED`;
        text.style.color = '#ffb700';
      }
    } else {
      text.textContent = 'CLEAR (Safe)';
      text.style.color = '#3ecf8e';
    }
  }
  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 12; c++) {
      const dot = document.getElementById(`led_${r}_${c}`);
      if (dot && bitmap[r]) {
        const isOn = bitmap[r][c] === 1;
        const ledColor = isAiGenerated ? '#e879f9' : '#ffb700';
        dot.style.background = isOn ? ledColor : '#1a1e26';
        dot.style.boxShadow = isOn ? `0 0 6px ${ledColor}` : 'none';
      }
    }
  }
}

ui.on_message('led_status', async msg => {
  if (msg) {
    renderVirtualMatrix(msg.trigger || 'clear', msg.bitmap || null, msg.ai_generated || false);
  }
});
ui.on_message('sync_icons', async cache => {
  if (cache) {
    matrixBitmaps = cache;
    renderVirtualMatrix('clear');
  }
});
ui.on_message('knob_update', async msg => {
  if (msg && msg.threshold != null) {
    const confidenceInput = document.getElementById('confidenceInput');
    const confidenceSlider = document.getElementById('confidenceSlider');
    if (confidenceInput && confidenceSlider) {
      confidenceInput.value = msg.threshold.toFixed(2);
      confidenceSlider.value = msg.threshold;
      updateConfidenceDisplay();
    }
  }
});
ui.on_message('hub_status', async status => {
  const badge = document.getElementById('hubStatusBadge');
  const text = document.getElementById('hubStatusText');
  if (badge && text && status) {
    if (status.online) {
      badge.className = 'hub-status online';
      text.textContent = `Hub Connected: ${status.host}:${status.port} (${status.method})`;
    } else {
      badge.className = 'hub-status offline';
      text.textContent = `Hub Offline: ${status.host}:${status.port} (${status.method})`;
    }
  }
});
ui.on_message('follow_status', async s => {
  const el = document.getElementById('followStatus');
  if (!el || !s) return;
  let text;
  if (s.state === 'following') {
    text = `Following: ${s.identity} (Track ${s.track_id}${s.priority != null ? `, P${s.priority}` : ''})`;
  } else if (s.state === 'known_target_missing') {
    text = `Holding for ${s.identity} (${s.missing_frames}/${s.grace_frames} frames)`;
  } else if (s.state === 'fallback_unknown') {
    text = `Following unknown (Track ${s.track_id})`;
  } else {
    text = 'No target';
  }
  el.textContent = text;
});
ui.on_message('robot_move_status', async status => {
  const statusElement = document.getElementById('robotStatus');
  if (!statusElement || !status) return;

  if (!status.ok) {
    statusElement.textContent = status.error || 'Command failed';
    statusElement.className = 'robot-status error';
  } else if (status.direction === 'STOP') {
    statusElement.textContent = 'Stopped';
    statusElement.className = 'robot-status stopped';
  } else {
    const isTurn = status.unit === 'degrees' || status.direction === 'LEFT' || status.direction === 'RIGHT';
    statusElement.textContent = isTurn
      ? `${status.direction} ${status.magnitude}°`
      : `${status.direction} for ${status.magnitude} second${status.magnitude === 1 ? '' : 's'}`;
    statusElement.className = 'robot-status active';
  }
});

ui.on_message('buzzer_status', async status => {
  const statusElement = document.getElementById('edgeBuzzerStatus');
  if (!statusElement || !status) return;

  if (!status.ok) {
    statusElement.textContent = status.error || 'Buzzer command failed';
    statusElement.className = 'robot-status error';
  } else if (status.action === 'stop' || status.action === 'notone') {
    statusElement.textContent = 'Buzzer Stopped';
    statusElement.className = 'robot-status stopped';
  } else if (status.action === 'believer') {
    statusElement.textContent = 'Playing "Believer" Melody 🎵';
    statusElement.className = 'robot-status active';
  } else {
    statusElement.textContent = `Tone: ${status.frequency || 440} Hz (${status.duration || 0} ms)`;
    statusElement.className = 'robot-status active';
  }
});

// Start the application
initVirtualMatrix();
initializeConfidenceSlider();
initializeRobotConsole();
initializeBuzzerConsole();
updateFeedback(null);
renderDetections();
ui.send_message('request_icons', {});

// Popover logic
const confidencePopoverText =
  'Minimum confidence score for detected objects. Lower values show more results but may include false positives.';
const feedbackPopoverText =
  'When the camera detects an object like cat, cell phone, clock, cup, dog or potted plant, a picture and a message will be shown here.';

document.querySelectorAll('.info-btn.confidence').forEach(img => {
  const popover = img.nextElementSibling;
  img.addEventListener('mouseenter', () => {
    popover.textContent = confidencePopoverText;
    popover.style.display = 'block';
  });
  img.addEventListener('mouseleave', () => {
    popover.style.display = 'none';
  });
});

document.querySelectorAll('.info-btn.feedback').forEach(img => {
  const popover = img.nextElementSibling;
  img.addEventListener('mouseenter', () => {
    popover.textContent = feedbackPopoverText;
    popover.style.display = 'block';
  });
  img.addEventListener('mouseleave', () => {
    popover.style.display = 'none';
  });
});

function onUIConnected() {
  if (errorContainer) {
    errorContainer.style.display = 'none';
    errorContainer.textContent = '';
  }
}

function onUIDisconnected() {
  if (errorContainer) {
    errorContainer.textContent = 'Connection to the board lost. Please check the connection.';
    errorContainer.style.display = 'block';
  }
}

function updateFeedback(detection) {
  const objectInfo = {
    cat: { text: 'Meow!', gif: 'cat.webp' },
    'cell phone': { text: 'Stay connected', gif: 'phone.webp' },
    clock: { text: 'Time to go', gif: 'clock.webp' },
    cup: { text: 'Need a break?', gif: 'cup.webp' },
    dog: { text: 'Walkies?', gif: 'dog.webp' },
    person: { text: 'Person Detected! LED Array Active 🚨', gif: 'hand.gif' },
    'potted plant': { text: 'Glow your ideas!', gif: 'plant.webp' },
  };

  if (detection && objectInfo[detection.content]) {
    const info = objectInfo[detection.content];
    const confidence = Math.floor(detection.confidence * 100);
    feedbackContentElement.innerHTML = `
            <div class="feedback-detection">
                <div class="percentage">${confidence}%</div>
                <img src="img/${info.gif}" alt="${detection.content}">
                <p>${info.text}</p>
            </div>
        `;
  } else {
    feedbackContentElement.innerHTML = `
            <img src="img/stars.svg" alt="Stars">
            <p class="feedback-text">System response will appear here</p>
        `;
  }
}

function printDetection(newDetection) {
  scans.unshift(newDetection);
  if (scans.length > MAX_RECENT_SCANS) {
    scans.pop();
  }
}

// Function to render the list of scans
function renderDetections() {
  // Clear the list
  recentDetectionsElement.innerHTML = ``;

  if (scans.length === 0) {
    recentDetectionsElement.innerHTML = `
            <div class="no-recent-scans">
                <img src="./img/no-face.svg">
                No object detected yet
            </div>
        `;
    return;
  }

  scans.forEach(scan => {
    const row = document.createElement('div');
    row.className = 'scan-container';

    // Create a container for content and time
    const cellContainer = document.createElement('span');
    cellContainer.className = 'scan-cell-container cell-border';

    // Content (text + icon)
    const contentText = document.createElement('span');
    contentText.className = 'scan-content';
    const value = scan.confidence;
    const result = Math.floor(value * 1000) / 10;
    contentText.innerHTML = `${result}% - ${scan.content}`;

    // Time
    const timeText = document.createElement('span');
    timeText.className = 'scan-content-time';
    timeText.textContent = new Date(scan.timestamp).toLocaleString('it-IT').replace(',', ' -');

    // Append content and time to the container
    cellContainer.appendChild(contentText);
    cellContainer.appendChild(timeText);

    row.appendChild(cellContainer);
    recentDetectionsElement.appendChild(row);
  });
}

function initializeConfidenceSlider() {
  const confidenceSlider = document.getElementById('confidenceSlider');
  const confidenceInput = document.getElementById('confidenceInput');
  const confidenceResetButton = document.getElementById('confidenceResetButton');

  confidenceSlider.addEventListener('input', updateConfidenceDisplay);
  confidenceInput.addEventListener('input', handleConfidenceInputChange);
  confidenceInput.addEventListener('blur', validateConfidenceInput);
  updateConfidenceDisplay();

  confidenceResetButton.addEventListener('click', e => {
    if (e.target.classList.contains('reset-icon') || e.target.closest('.reset-icon')) {
      resetConfidence();
    }
  });
}

function handleConfidenceInputChange() {
  const confidenceInput = document.getElementById('confidenceInput');
  const confidenceSlider = document.getElementById('confidenceSlider');

  let value = parseFloat(confidenceInput.value);

  if (isNaN(value)) value = 0.5;
  if (value < 0) value = 0;
  if (value > 1) value = 1;

  confidenceSlider.value = value;
  updateConfidenceDisplay();
}

function validateConfidenceInput() {
  const confidenceInput = document.getElementById('confidenceInput');
  let value = parseFloat(confidenceInput.value);

  if (isNaN(value)) value = 0.5;
  if (value < 0) value = 0;
  if (value > 1) value = 1;

  confidenceInput.value = value.toFixed(2);

  handleConfidenceInputChange();
}

function updateConfidenceDisplay() {
  const confidenceSlider = document.getElementById('confidenceSlider');
  const confidenceInput = document.getElementById('confidenceInput');
  const confidenceValueDisplay = document.getElementById('confidenceValueDisplay');
  const sliderProgress = document.getElementById('sliderProgress');

  const value = parseFloat(confidenceSlider.value);
  ui.send_message('override_th', value); // Send confidence to backend
  const percentage = ((value - confidenceSlider.min) / (confidenceSlider.max - confidenceSlider.min)) * 100;

  const displayValue = value.toFixed(2);
  confidenceValueDisplay.textContent = displayValue;

  if (document.activeElement !== confidenceInput) {
    confidenceInput.value = displayValue;
  }

  sliderProgress.style.width = percentage + '%';
  confidenceValueDisplay.style.left = percentage + '%';
}

function resetConfidence() {
  const confidenceSlider = document.getElementById('confidenceSlider');
  const confidenceInput = document.getElementById('confidenceInput');

  confidenceSlider.value = '0.5';
  confidenceInput.value = '0.50';
  updateConfidenceDisplay();
}

function initializeRobotConsole() {
  const magnitudeInput = document.getElementById('robotMagnitude');
  const stopButton = document.getElementById('robotStop');

  document.querySelectorAll('.robot-move').forEach(button => {
    button.addEventListener('click', () => {
      let magnitude = Number.parseInt(magnitudeInput.value, 10);
      if (!Number.isFinite(magnitude)) magnitude = 1;
      magnitude = Math.min(360, Math.max(1, magnitude));
      magnitudeInput.value = magnitude;

      ui.send_message('robot_move', {
        direction: button.dataset.direction,
        magnitude,
      });
    });
  });

  stopButton.addEventListener('click', () => {
    ui.send_message('robot_move', { direction: 'STOP', magnitude: 1 });
  });
}

function initializeBuzzerConsole() {
  const freqInput = document.getElementById('edgeBuzzerFreq');
  const durInput = document.getElementById('edgeBuzzerDur');
  const toneBtn = document.getElementById('btnEdgeBuzzerTone');
  const believerBtn = document.getElementById('btnEdgeBuzzerBeliever');
  const stopBtn = document.getElementById('btnEdgeBuzzerStop');

  if (toneBtn) {
    toneBtn.addEventListener('click', () => {
      const frequency = Number.parseInt(freqInput.value, 10) || 440;
      const duration = Number.parseInt(durInput.value, 10) || 0;
      ui.send_message('buzzer', { action: 'start', frequency, duration });
    });
  }

  if (believerBtn) {
    believerBtn.addEventListener('click', () => {
      ui.send_message('buzzer', { action: 'believer' });
    });
  }

  if (stopBtn) {
    stopBtn.addEventListener('click', () => {
      ui.send_message('buzzer', { action: 'stop' });
    });
  }
}
