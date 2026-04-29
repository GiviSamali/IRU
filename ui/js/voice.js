const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let isRecording = false;

const voiceBtn = document.getElementById('voiceBtn');
if (!SpeechRecognition) {
  if (voiceBtn) {
    voiceBtn.disabled = true;
    voiceBtn.title = 'Голосовой ввод не поддерживается этим браузером. Откройте в Chrome или Edge';
  }
} else if (voiceBtn) {
  voiceBtn.addEventListener('click', toggleVoice);
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && (e.key === 'M' || e.key === 'm')) {
      e.preventDefault();
      toggleVoice();
    }
  });
}

function toggleVoice() {
  if (isRecording) { stopVoice(); } else { startVoice(); }
}

function startVoice() {
  if (!SpeechRecognition) return;
  recognition = new SpeechRecognition();
  recognition.lang = 'ru-RU';
  recognition.interimResults = true;
  recognition.continuous = true;

  const input = document.getElementById('chatInput');
  const initialValue = input.value;
  let finalTranscript = '';

  recognition.onstart = () => {
    isRecording = true;
    voiceBtn.classList.add('recording');
    voiceBtn.setAttribute('aria-label', 'Остановить запись');
  };

  recognition.onresult = (event) => {
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const transcript = event.results[i][0].transcript;
      if (event.results[i].isFinal) {
        finalTranscript += transcript;
      } else {
        interim += transcript;
      }
    }
    const sep = initialValue && !initialValue.endsWith(' ') ? ' ' : '';
    input.value = initialValue + sep + finalTranscript + interim;
    if (input.tagName === 'TEXTAREA') {
      input.style.height = 'auto';
      input.style.height = input.scrollHeight + 'px';
    }
    updateCharCount();
  };

  recognition.onerror = (event) => {
    console.warn('Speech recognition error:', event.error);
    if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
      alert('Доступ к микрофону запрещён. Разрешите в настройках браузера.');
    }
    stopVoice();
  };

  recognition.onend = () => { stopVoice(); };

  try { recognition.start(); } catch (err) {
    console.warn('Не удалось запустить распознавание:', err);
    stopVoice();
  }
}

function stopVoice() {
  isRecording = false;
  if (voiceBtn) {
    voiceBtn.classList.remove('recording');
    voiceBtn.setAttribute('aria-label', 'Включить микрофон');
  }
  if (recognition) {
    try { recognition.stop(); } catch (_) {}
    recognition = null;
  }
}
