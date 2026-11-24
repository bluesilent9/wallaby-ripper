'use strict';
const $ = id => document.getElementById(id);
const token = location.pathname.split('/')[1];
let selected = null, busy = false, backendReady = false;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {cache: 'no-store'} : {
    method: 'POST', headers: {'X-Wallaby-Token': token}, body
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The request failed.');
  return result;
}
function controls() { $('start').disabled = busy || !selected || !backendReady; $('file').disabled = busy; }
function choose(file) {
  if (busy || !file) return;
  if (!file.name.toLowerCase().endsWith('.acsm') || file.size > 2 * 1024 * 1024) {
    show('Choose an ACSM file smaller than 2 MB.', true, 0); return;
  }
  selected = file;
  $('file-name').textContent = file.name;
  $('file-detail').textContent = Math.max(1, Math.ceil(file.size / 1024)) + ' KB · Ready to convert';
  $('progress').classList.add('hidden');
  controls();
}
function show(message, error, percent) {
  $('progress').classList.remove('hidden');
  $('progress').classList.toggle('error', !!error);
  $('progress-text').textContent = message;
  $('progress-bar').style.width = percent + '%';
  const stage = percent >= 100 ? 5 : percent >= 90 ? 4 : percent >= 70 ? 3 : percent >= 35 ? 2 : percent > 0 ? 1 : 0;
  $('progress-bar').setAttribute('aria-valuenow', String(stage));
  $('progress-bar').setAttribute('aria-valuetext', error ? message : 'Stage ' + stage + ' of 5');
  Array.from($('stages').children).forEach((item, index) => {
    item.classList.toggle('complete', index + 1 < stage || stage === 5);
    item.classList.toggle('active', index + 1 === stage && stage !== 5);
    if (index + 1 === stage) item.setAttribute('aria-current', 'step'); else item.removeAttribute('aria-current');
  });
}
async function refresh() {
  try {
    const status = await api('api/status');
    backendReady = status.backend === 'ready';
    $('backend').textContent = backendReady ? 'Ready · Authorization handled automatically' :
      status.backend === 'error' ? status.error : 'First launch: building local processing tools. This may take a minute…';
    $('auth-status').textContent = status.authorization;
    controls();
    if (status.backend !== 'error' && !backendReady) setTimeout(refresh, 1000);
  } catch (error) { $('backend').textContent = 'Connection lost. Reopen Wallaby Ripper from your application menu.'; }
}
$('file').addEventListener('change', event => choose(event.target.files[0]));
['dragenter', 'dragover'].forEach(name => $('dropzone').addEventListener(name, event => {
  event.preventDefault(); $('dropzone').classList.add('dragging');
}));
['dragleave', 'drop'].forEach(name => $('dropzone').addEventListener(name, event => {
  event.preventDefault(); $('dropzone').classList.remove('dragging');
}));
$('dropzone').addEventListener('drop', event => choose(event.dataTransfer.files[0]));
$('start').addEventListener('click', async () => {
  if (!selected || busy) return;
  busy = true; controls(); $('download').classList.add('hidden'); show('Starting…', false, 5);
  try {
    const job = await api('api/convert', selected);
    const percentages = {queued: 5, authorizing: 15, downloading: 35, decrypting: 70, checking: 90, ready: 100};
    while (true) {
      const status = await api('api/jobs/' + job.id);
      if (status.state === 'error') throw new Error(status.message);
      show(status.message, false, percentages[status.state] || 5);
      if (status.state === 'ready') {
        const link = $('download');
        link.href = 'download/' + job.id; link.download = status.filename;
        link.classList.remove('hidden'); link.click();
        show('PDF ready. Your browser download has started. The temporary encrypted PDF has been deleted.', false, 100);
        break;
      }
      await delay(700);
    }
  } catch (error) { show(error.message, true, 0); }
  finally { busy = false; controls(); refresh(); }
});
$('settings-toggle').addEventListener('click', () => {
  const hidden = $('settings').classList.toggle('hidden');
  $('settings-toggle').setAttribute('aria-expanded', String(!hidden));
});
$('login').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.submitter || $('login').querySelector('button');
  button.disabled = true; $('settings-message').textContent = 'Authorizing this computer…';
  try {
    const password = $('password').value; $('password').value = '';
    await api('api/activate', JSON.stringify({email: $('email').value, password}));
    $('settings-message').textContent = 'Adobe authorization saved. You can convert your ticket now.';
    refresh();
  } catch (error) { $('settings-message').textContent = error.message; }
  finally { button.disabled = false; }
});
$('import').addEventListener('click', async () => {
  $('import').disabled = true;
  try {
    const payload = {};
    for (const file of $('auth-files').files) {
      if (!['device.xml', 'activation.xml', 'devicesalt'].includes(file.name) || file.size > 100000) throw new Error('Select only device.xml, activation.xml and devicesalt.');
      const bytes = new Uint8Array(await file.arrayBuffer());
      let value = ''; for (const byte of bytes) value += String.fromCharCode(byte);
      payload[file.name] = btoa(value);
    }
    await api('api/import', JSON.stringify(payload));
    $('settings-message').textContent = 'Authorization imported.'; refresh();
  } catch (error) { $('settings-message').textContent = error.message; }
  finally { $('import').disabled = false; }
});
refresh();
