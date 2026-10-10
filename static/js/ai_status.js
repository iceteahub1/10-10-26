'use strict';
fetch('/health').then(response => response.json()).then(status => {
  const node = document.getElementById('ai-status');
  node.textContent = status.ai_available
    ? `Selected model available: ${status.ai_model}. AI chat is available through ai_chat.bat; this dashboard has no chat interface.`
    : `AI chat unavailable. ${status.ai_reason} Double-click configure_ai.bat to enable AI later. Non-AI ordering is available through ordering.bat.`;
}).catch(() => {
  document.getElementById('ai-status').textContent = 'AI status could not be checked. Use configure_ai.bat to check your model.';
});
