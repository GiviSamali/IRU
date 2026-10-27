/* One deterministic presentation adapter. No requests, execution or voice lifecycle. */
const IRUSmartUI = (() => {
  const STATUS = Object.freeze({
    pending: 'waiting', waiting: 'waiting', queued: 'waiting', waiting_dependency: 'waiting', confirm: 'waiting', waiting_confirmation:'waiting', waiting_input:'waiting',
    thinking: 'running', running: 'running', running_tool: 'running', waiting_agent: 'running',
    preparing_runtime: 'running', refreshing_state: 'running', writing_file: 'running',
    launching_app: 'running', restoring: 'running', cancelling: 'running',
    done: 'success', completed: 'success', completed_with_recovery: 'success', recovered: 'success',
    success:'success', ok:'success', verified:'success', opened_verified:'success', focused:'success', found:'success',
    started:'running', launch_requested:'waiting', unknown:'unknown', needs_verification:'blocked', stale_element:'blocked', disconnected:'blocked',
    not_found:'failed', action_not_verified:'failed', timeout:'failed',
    partial: 'partial', partial_failure: 'partial', blocked: 'blocked', skipped: 'blocked',
    failed: 'failed', error: 'failed', cancelled: 'cancelled', canceled: 'cancelled',
  });
  const LABELS = Object.freeze({ waiting: 'Ожидает', running: 'Выполняется', success: 'Завершено',
    partial: 'Частично выполнено', blocked: 'Заблокировано', unknown:'Результат не подтверждён', failed: 'Ошибка', cancelled: 'Отменено' });
  const key = value => typeof value === 'string' ? value.trim().toLowerCase() : '';
  const list = value => Array.isArray(value) ? value.filter(item => item && typeof item === 'object') : [];
  const text = value => typeof value === 'string' ? value : '';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const normalizeStatus = value => Object.hasOwn(STATUS, key(value)) ? STATUS[key(value)] : null;

  function commandState(command) {
    const result = command?.result || {};
    if (result.error) return 'failed';
    if (result.returncode != null) {
      if (!Number.isInteger(result.returncode)) return 'blocked';
      if (result.returncode !== 0) return 'failed';
    }
    const resultState = normalizeStatus(result.status);
    if (['failed', 'blocked', 'unknown', 'partial', 'cancelled', 'waiting', 'running'].includes(resultState)) return resultState;
    const explicit = normalizeStatus(command?.status || command?.tool_status);
    if (explicit) return explicit;
    if (resultState) return resultState;
    if (result.returncode === 0) return 'success'; // execution status, never goal completion
    return 'unknown';
  }

  const ANSWER_STATUS = Object.freeze({
    'answer.report_failure':'failed', 'answer_report_failure':'failed',
    'answer.ask_clarification':'waiting', 'answer_ask_clarification':'waiting',
    'answer.request_confirmation':'waiting', 'answer_request_confirmation':'waiting',
    partial_report:'partial', error_report:'failed', failure:'failed', clarification:'waiting',
  });
  function lastTerminal(commands) {
    return [...commands].reverse().find(command => key(command.status) === 'terminal' && key(command.tool_name || command.action).startsWith('answer'));
  }
  function taskState(message, tasks, commands = []) {
    const report = message.workerReport || message.worker_report;
    if (report?.schema_version === 1) return normalizeStatus(report.status) || 'unknown';
    // Old history is normalized on the server. Browser prose/steps cannot
    // establish completion when an authoritative report is unavailable.
    const raw = normalizeStatus(message.taskStatus || message.task_status);
    if (message.confirmTaskId || message.planReview) return 'waiting';
    if (message.loading) return 'running';
    return ['running','waiting','queued','waiting_confirmation','waiting_input','waiting_dependency','cancelled','failed','partial','blocked','unknown'].includes(raw) ? raw : 'unknown';
  }

  function filesFromCommands(commands) {
    const files = new Map();
    for (const command of commands) {
      const result = command.result;
      if (!result || typeof result !== 'object' || result.error) continue;
      const tool = key(command.tool_name || command.action);
      // Legacy get_file_link emits url/file_path/device without a separate status field.
      // Accept that exact structured contract, never the same URL found in answer text.
      const legacyFileLink = tool === 'get_file_link'
        && !command.status && !command.tool_status && !result.status && result.returncode == null;
      if (commandState(command) !== 'success' && !legacyFileLink) continue;
      let path = '', device = text(command.target_device_id || command.device_id), size;
      if (tool === 'write_content') {
        // The established write_content contract supplies actual byte counts.
        if (!Number.isFinite(result.bytes_written) || result.bytes_written < 0) continue;
        path = text(result.path || result.file_path); size = result.total_size ?? result.bytes_written;
      } else if (tool === 'transfer_file') {
        if (result.status !== 'success' || result.sha256_verified !== true) continue;
        path = text(result.target_path); device = text(result.target_device); size = result.bytes_transferred;
      } else if (tool === 'get_file_link' || tool === 'download_file' || tool === 'get_file_content') {
        // Existing server-created download metadata, never a URL parsed from model prose.
        if (!/^\/api\/download\/[a-f0-9-]+$/i.test(text(result.url))) continue;
        path = text(result.file_path); size = result.size;
      } else continue;
      if (!path || !device || /[\x00-\x1f]/.test(path + device)) continue;
      try { encodeURIComponent(path); encodeURIComponent(device); } catch { continue; }
      const name = path.split(/[\\/]/).pop();
      if (!name) continue;
      const id = `${device}:${path}`;
      if (!files.has(id)) files.set(id, { type:'file', path, device, name,
        extension: name.includes('.') ? name.split('.').pop().toLowerCase() : '',
        size: Number.isFinite(size) && size >= 0 ? size : null });
    }
    return [...files.values()];
  }

  function filesFromGoal(presentation) {
    if(!presentation || !Array.isArray(presentation.result_artifacts) || !Array.isArray(presentation.task_ids))return [];
    const files=new Map();
    for(const artifact of presentation.result_artifacts) {
      if(!artifact || artifact.verified!==true || !presentation.task_ids.includes(artifact.task_id))continue;
      const path=text(artifact.path), device=text(artifact.device_id), name=path.split(/[\\/]/).pop();
      if(!path || !device || !name || /[\x00-\x1f]/.test(path+device))continue;
      try {encodeURIComponent(path);encodeURIComponent(device);} catch {continue;}
      // Identity is device + full path; two devices may return the same filename.
      const identity=`${device}:${path.replace(/\\/g,'/').toLowerCase()}`;
      if(!files.has(identity))files.set(identity,{type:'file',path,device,name,verified:true,
        contentVerified:artifact.content_verified===true,verificationScope:artifact.verification_scope || 'file_identity',
        extension:name.includes('.')?name.split('.').pop().toLowerCase():'',
        size:Number.isFinite(artifact.size_bytes) && artifact.size_bytes>=0?artifact.size_bytes:null});
    }
    return [...files.values()];
  }

  function adapt(message, index = 0, chatId = '') {
    const m = message && typeof message === 'object' ? message : {};
    const encodeId = (value, fallback) => {
      if (!['string','number'].includes(typeof value)) return String(fallback);
      try { return encodeURIComponent(String(value)); } catch { return String(fallback); }
    };
    const id = `smart-${encodeId(chatId,'')}-${encodeId(m._taskId ?? m.id ?? index,index)}`;
    const candidate=m.goalPresentation || m.goal_presentation;
    const goal=candidate?.goal_id && candidate.goal_id===(m.goalId||m.goal_id) ? candidate : null;
    const isGoalMessage=m.taskKind==='goal' || Boolean(m.resultFirst && (m.goalId||m.goal_id));
    const goalFiles=filesFromGoal(goal);
    const blocks = [];
    let content = text(m.conversationalResponse) || text(m.conversational_response) || text(m.content) || text(m.text);
    const workerReport=m.workerReport || m.worker_report;
    if(m.role!=='user' && !goal && workerReport?.schema_version===1
        && ['partial','unknown'].includes(workerReport.status) && text(workerReport.summary))content=text(workerReport.summary);
    // Replace only exact server-generated file lines represented by real File blocks.
    // Keep all other prose, statuses and unavailable/unverified paths unchanged.
    if(m.role!=='user' && goalFiles.length)content=content.split('\n').map(line=>{
      const value=line.trim();
      for(const file of goalFiles) {
        const prefix='Файл: '+file.path;
        if(value===prefix)return null;
        // The server appends this footer to the last file line for partial goals.
        // Remove only the known file prefix; preserve the warning itself.
        if(value===prefix+' Подробности по отдельным поручениям — в Операциях.')return 'Подробности по отдельным поручениям — в Операциях.';
      }
      return line;
    }).filter(line=>line!==null).join('\n').trim();
    const goalSummary=m.role!=='user' && goal && ['running','success','partial','failed','blocked','unknown','cancelled'].includes(goal.status)
      && Number.isInteger(goal.completed_count) && Number.isInteger(goal.total_count)
      && goal.total_count>0 && goal.completed_count>=0 && goal.completed_count<=goal.total_count;
    if(goalSummary) {
      const source=text(m.conversationalResponse)||text(m.conversational_response)||text(m.content)||text(m.text);
      const canonical=source===text(goal.message);
      blocks.push({type:'goal',key:id+'-goal',goalId:goal.goal_id,status:goal.status,
        done:goal.completed_count,total:goal.total_count,attention:goal.requires_user_action===true,
        message:text(goal.message),
        details:canonical?content:text(goal.message)});
      // Replace only the server Goal summary; preserve independent answer text.
      if(canonical)content='';
    }
    const executionDetails = text(m.executionDetails) || text(m.execution_details);
    if (content) blocks.push({ type:'text', text:content, key:id+'-text', long:content.length > 200 || content.split('\n').length > 4 });
    if (m.role === 'user') return { key:id, blocks };
    const tasks = list(m.loading ? m.liveTasks || m.tasks : m.tasks);
    const commands = list(m.loading ? m.liveCommands || m.commands : m.commands);
    const operations = commands.filter(command => !key(command.tool_name || command.action).startsWith('answer'));
    const conversationNegative = [m.taskStatus, (m.taskReceipt || m.task_receipt || {}).task_status, m.overallStatus].some(value => ['failed','blocked','partial','unknown','cancelled'].includes(normalizeStatus(value)));
    const pureConversation = !executionDetails && !m.loading && !m.confirmTaskId && !m.planReview && !conversationNegative && !tasks.length && !operations.length
      && (key(lastTerminal(commands)?.result?.answer_type) === 'pure_text' || (m.taskMode === 'conversation' && normalizeStatus(m.taskStatus) === 'success'));
    const hasTask = !pureConversation && (m.workerReport || m.worker_report || tasks.length || operations.length || m.loading || m.taskStatus || m.taskReceipt || m.task_receipt || m.confirmTaskId || m.planReview || executionDetails);
    if (hasTask && !goalSummary) {
      const status = taskState(m, tasks, commands);
      const receipt = m.taskReceipt || m.task_receipt || {};
      const detailed = status !== 'success' || m.loading || m.confirmTaskId || m.planReview || m.cancelAvailable
        || m.taskMode === 'plan' || receipt.answer_source === 'pipeline_step_report' || tasks.length > 0
        || operations.length !== 1 || !Number.isFinite(m.taskElapsedMs) || m.taskElapsedMs >= 30000
        || operations.some(command => command.step_index != null || commandState(command) !== 'success');
      blocks.push({ type:'task', key:id+'-task', status, label:LABELS[status], compact:!detailed,
        title:text(m.taskTitle) || text(tasks[0]?.goal) || 'Выполнение запроса',
        summary: receipt.goal_completed === false ? 'Исходная цель не завершена.' : '',
        tasks, commands, executionDetails, loading:Boolean(m.loading), currentStatus:m.currentStatus,
        unknown: status === 'unknown' });
    }
    const report=m.workerReport || m.worker_report;
    const files=goal || isGoalMessage ? goalFiles : filesFromCommands(report?.schema_version===1
      ? commands.filter(command=>['get_file_link','download_file'].includes(key(command.tool_name || command.action))) : commands);
    if(!goal && !isGoalMessage && report?.schema_version===1 && Array.isArray(report.artifacts) && Array.isArray(report.target_device_ids)) {
      for(const artifact of report.artifacts) {
        if(!artifact || artifact.verified!==true || !report.target_device_ids.includes(artifact.device_id))continue;
        const path=text(artifact.path), device=text(artifact.device_id), name=path.split(/[\\/]/).pop();
        if(!path || !device || !name || /[\x00-\x1f]/.test(path+device))continue;
        try {encodeURIComponent(path);encodeURIComponent(device);} catch {continue;}
        let file=files.find(file=>file.path===path && file.device===device);
        if(!file){file={type:'file',path,device,name,
          extension:name.includes('.')?name.split('.').pop().toLowerCase():'',size:null};files.push(file);}
        file.verified=true;
        file.contentVerified=artifact.content_verified===true;
        file.verificationScope=artifact.verification_scope || 'file_identity';
      }
    }
    blocks.push(...files.map((file,i)=>({...file,key:id+'-file-'+i})));
    if (m.confirmTaskId || (m.suggestedFact?.text && !m.suggestedFactDeclined) || m.cancelAvailable) {
      blocks.push({ type:'action', key:id+'-action', index, critical:Boolean(m.confirmTaskId), message:m });
    }
    return { key:id, blocks };
  }

  function formattedText(value) {
    const lines = String(value ?? '').replace(/\r\n?/g, '\n').split('\n');
    const output = [];
    let paragraph = [], code = null;
    const inline = line => line.split(/(`[^`]*`)/g).map((part, index) => index % 2
      ? esc(part) : esc(part).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')).join('');
    const flush = () => { if (paragraph.length) { output.push(`<p>${paragraph.map(inline).join('<br>')}</p>`); paragraph = []; } };
    for (const line of lines) {
      if (/^\s*```/.test(line)) {
        flush();
        if (code !== null) { output.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`); code = null; }
        else code = [];
      } else if (code !== null) code.push(line);
      else {
        const heading = line.match(/^ {0,3}(#{1,3}) +(.+?)\s*#*$/);
        if (heading) { flush(); output.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`); }
        else if (!line.trim()) flush();
        else paragraph.push(line);
      }
    }
    flush();
    if (code !== null) output.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
    return output.join('');
  }

  const expanded = (block, context) => Boolean(context.expanded?.has(block.key));
  const REGISTRY = Object.freeze({
    goal(block,context) {
      const labels={running:'Выполняется',success:'Готово',partial:'Частично выполнено',failed:'Не выполнено',blocked:'Заблокировано',unknown:'Не подтверждено',cancelled:'Отменено'};
      const icon=block.status==='success'?'✓':block.status==='running'?'':block.status==='partial'?'◐':block.status==='cancelled'?'—':'!';
      const percent=100*block.done/block.total;
      return `<section class="smart-block smart-goal" data-block-type="goal" data-status="${esc(block.status)}" data-single-operation="${block.total===1}" data-goal-id="${esc(block.goalId||'')}" data-smart-key="${esc(block.key)}" aria-label="Итог цели">
        <div class="smart-goal-heading"><span class="smart-goal-icon" aria-hidden="true">${icon}</span><strong>Поручение</strong></div>
        <div class="smart-goal-message">${formattedText(block.message || block.details || '')}</div>
        ${block.attention?'<span class="smart-goal-attention">Нужен ответ</span>':''}
        <details class="smart-goal-details"${expanded(block,context)?' open':''}><summary data-action="toggle-goal-result" aria-expanded="${expanded(block,context)}">Ход выполнения</summary><div>${esc(labels[block.status])}: <span class="smart-goal-count">${block.done} / ${block.total}</span><div class="smart-goal-meter" role="meter" aria-label="Подтверждённые результаты" aria-valuemin="0" aria-valuemax="${block.total}" aria-valuenow="${block.done}"><span style="width:${percent}%"></span></div></div></details>
      </section>`;
    },
    text(block, context) {
      const open = expanded(block, context);
      return `<div class="smart-block smart-text${block.long ? ' collapsible' : ''}${open ? ' expanded' : ''}" data-block-type="text" data-smart-key="${esc(block.key)}">
        <div class="smart-text-content${context.formatAssistantText ? ' formatted' : ''}" id="${esc(block.key)}">${context.formatAssistantText ? formattedText(block.text) : esc(block.text)}</div>${block.long ? `<button type="button" class="smart-text-toggle" data-action="toggle-smart-text" aria-controls="${esc(block.key)}" aria-expanded="${open}">${open ? 'Свернуть текст' : 'Полный текст'}</button>` : ''}</div>`;
    },
    task(block, context) {
      const open = expanded(block, context), details = context.taskDetails ? context.taskDetails(block) : '';
      const reportClass = block.executionDetails ? ' with-execution-report' : '';
      const summary = block.unknown ? 'Результат цели не подтверждён.' : block.summary;
      if (block.compact) {
        return `<section class="smart-block smart-task compact-result${reportClass}${open ? ' expanded' : ''}" data-block-type="task" data-status="${block.status}" data-smart-key="${esc(block.key)}" aria-label="Итог задачи">
          <div class="smart-task-heading"><span class="smart-status">✓ ${esc(block.label)}</span>${details ? `<button type="button" class="smart-task-toggle" data-action="toggle-smart-details" aria-expanded="${open}" aria-controls="${esc(block.key)}">${open ? 'Свернуть подробности' : 'Подробности'}</button>` : ''}</div>
          ${details ? `<div class="smart-task-details" id="${esc(block.key)}">${details}</div>` : ''}</section>`;
      }
      return `<section class="smart-block smart-task${reportClass}${open ? ' expanded' : ''}" data-block-type="task" data-status="${block.status}" data-smart-key="${esc(block.key)}" aria-label="Состояние задачи">
        <div class="smart-task-heading"><span class="smart-task-title">${esc(block.title)}</span><span class="smart-status">${esc(block.label)}</span></div>
        ${summary ? `<p class="smart-task-summary">${esc(summary)}</p>` : ''}
        ${details ? `<button type="button" class="smart-task-toggle" data-action="toggle-smart-details" aria-expanded="${open}" aria-controls="${esc(block.key)}">${open ? 'Свернуть подробности' : 'Ход выполнения'}</button><div class="smart-task-details" id="${esc(block.key)}">${details}</div>` : ''}
        </section>`;
    },
    file(block, context) {
      const open = expanded(block, context);
      return `<section class="smart-block smart-file${open ? ' expanded' : ''}" data-block-type="file" data-smart-key="${esc(block.key)}">
        <div class="smart-file-heading"><span class="smart-file-kind">${esc(block.extension || 'Файл')}</span><strong title="${esc(block.path)}">${esc(block.name)}</strong></div>
        ${block.verified?`<div class="smart-file-verified">${block.contentVerified?'Файл и полнота содержания подтверждены':block.verificationScope==='copy_identity'?'Подтверждена идентичность копии':'Файл подтверждён; полнота содержания не проверена'}</div>`:''}
        <div class="smart-file-meta"><span>${esc(block.device)}</span></div>
        <div class="smart-file-actions">
          <button type="button" data-action="open-message-file" data-device-id="${esc(encodeURIComponent(block.device))}" data-file-path="${esc(encodeURIComponent(block.path))}">Открыть на устройстве</button>
          <button type="button" data-action="reveal-message-file" data-device-id="${esc(encodeURIComponent(block.device))}" data-file-path="${esc(encodeURIComponent(block.path))}" title="Открыть папку на устройстве">Папка</button>
          <button type="button" class="msg-download-link" data-action="download-message-file" data-device-id="${esc(encodeURIComponent(block.device))}" data-file-path="${esc(encodeURIComponent(block.path))}">Скачать файл</button>
        </div></section>`;
    },
    action(block, context) {
      return `<section class="smart-block smart-action${block.critical ? ' critical' : ''}" data-block-type="action" data-smart-key="${esc(block.key)}">${block.critical ? '<div class="smart-action-label">Требуется ваше подтверждение</div>' : ''}${context.actionDetails ? context.actionDetails(block.message, block.index) : ''}</section>`;
    },
  });
  function render(view, context = {}) {
    return `<div class="smart-blocks">${list(view?.blocks).map(block => Object.hasOwn(REGISTRY, block.type) ? REGISTRY[block.type](block, context) : REGISTRY.text({ type:'text', text:text(block.text), key:view.key+'-fallback' }, context)).join('')}</div>`;
  }
  return Object.freeze({ STATUS, LABELS, REGISTRY, normalizeStatus, commandState, taskState, filesFromCommands, adapt, render });
})();
if (typeof module !== 'undefined' && module.exports) module.exports = IRUSmartUI;
