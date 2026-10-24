"""Reviewed architecture definitions. Keep separate from generic AST extraction.

Bindings name real definitions; missing symbols make snapshot generation fail.
Module membership is navigation, not a claim that all its functions implement a role.
"""

def binding(path, *symbols):
    return {'path': path, 'symbols': list(symbols)}

def mechanism(key, name, responsibility, inputs, outputs, trust, *bindings):
    return {'id': 'mechanism:'+key, 'name':name, 'type':'mechanism',
            'description':responsibility, 'responsibility':responsibility,
            'inputs':inputs, 'outputs':outputs, 'trust_boundaries':trust,
            'bindings':list(bindings), 'unknowns':['Полный перечень ошибок и косвенных динамических зависимостей не установлен.']}

def component(key, name, subsystem, responsibility, *mechanisms):
    return {'id':'component:'+key,'name':name,'type':'component','parent_id':f'group:{subsystem}',
            'description':responsibility,'responsibility':responsibility,'mechanisms':list(mechanisms)}

CATALOG = [
 component('orchestrator','Orchestrator',1,'Диалог, интерпретация текущего сообщения и handoff; исполнительные команды не выполняет.',
  mechanism('decision','Decision contract','Проверяет единственный структурированный результат orchestrator.decide через Decision.',
   ['message','context','user_id / chat_id / task_id'],['Decision','usage statistics'],['LLM output проверяется Pydantic; отсутствующие objective/target не разрешают делегацию.'],
   binding('server/orchestrator.py','Decision','decide')),
  mechanism('turn-handoff','Turn routing / handoff','Сохраняет turn, выбирает intent, проверяет ссылки на задания и вызывает переданный delegate при делегации.',
   ['cmd','authenticated user','chat_id','delegate callback'],['turn response','Worker admission or plan suggestion'],['Отмена требует точной ссылки; owned_job ограничивает задания владельцем.'],
   binding('server/orchestrator.py','run_turn','context_for','recover_turns'))),
 component('context','Context / Handoff',1,'Подготавливает ограниченные данные для Worker и controller; история не является разрешением.',
  mechanism('bounded-context','Bounded Worker context','Отбирает историю, проверяет принадлежность результатов и устройств, ограничивает размер handoff.',
   ['owner','chat_id','message','device_ids','history','source_ids'],['messages with data-only context'],['Только текущее human message даёт полномочия; старые basis не импортируются как доказательства.'],
   binding('server/worker_context.py','capture_history','source_references','build_worker_context'),binding('server/controller_shared.py','data_only_context')),
  mechanism('runtime-context','Controller runtime context','Собирает профиль, устройства и историю для выбора controller route.',
   ['device_info','all_devices','device_profile','user_id','chat_history'],['LLMRuntimeContext','route kwargs'],[],
   binding('server/controller.py','LLMRuntimeContext','_build_runtime_context'),binding('server/controller_shared.py','build_memory_block','build_chat_messages'))),
 component('worker','Worker',2,'Исполняет принятое поручение через существующий NL controller и журнал инструментов; не самостоятельный новый сервис.',
  mechanism('worker-lifecycle','Task execution lifecycle','Запускает задачу, сохраняет результаты устройств, обрабатывает остановку и передачу контекста.',
   ['task_id','user_id','message','device_ids','chat_id'],['task results','commands','answer'],['Перед исполнением проверяется владелец устройства; original_request сохраняет human authority.'],
   binding('server/task_runtime.py','run_nl_task','run_onboarding_task')),
  mechanism('worker-tool-loop','Tool execution loop','Выполняет итерации controller non-pipeline, обрабатывает tools и терминальный ответ по протоколу.',
   ['user_message','send_command_fn','runtime context','tools'],['answer','commands','training_context'],['Tool arguments, confirmations, evidence basis и аудит проверяются существующими модулями.'],
   binding('server/controller_non_pipeline.py','process_non_pipeline_command'),binding('server/controller.py','process_nl_command'))),
 component('scheduler','Scheduler',2,'Очередь Worker по владельцу, admission, выполнение и восстановление сохранённых заданий.',
  mechanism('admission','Queue admission','Транзакционно принимает задачу и создаёт сообщения и worker_jobs; запускает runner владельца.',
   ['task','request_key'],['queued/running task','runner'],['chat ownership; request_id conflict; лимит очереди и резерв quota.'],
   binding('server/worker_scheduler.py','WorkerScheduler','WorkerScheduler.submit','WorkerScheduler.ensure_runner')),
  mechanism('dispatch-recovery','Dispatch / restore / persistence','Вызывает task runtime, восстанавливает snapshot и сохраняет Worker Report.',
   ['owned worker_jobs','task snapshot'],['restored task','persisted report'],['owned_job фильтрует owner_user_id; журнал и отчёт принадлежат текущему task.'],
   binding('server/worker_scheduler.py','WorkerScheduler._execute','WorkerScheduler._run','restore_task','persist_report','owned_job'))),
 component('plan','PLAN',2,'Планирование и выполнение шагов pipeline с контекстом и проверкой целевого устройства.',
  mechanism('plan-contract','Plan normalization','Нормализует план и проверяет устройство шага; готовит shared/worker context.',
   ['raw_plan','goal','device_id','steps','all_devices'],['normalized plan','worker context'],['Шаг разрешён только для доступного целевого устройства.'],
   binding('server/controller_pipeline.py','normalize_pipeline_plan','validate_pipeline_step_device','build_pipeline_shared_context','build_pipeline_worker_context')),
  mechanism('plan-execution','Pipeline step execution','Исполняет pipeline workers, собирает команды и итоговую task receipt.',
   ['user message','devices','steps','execution callbacks'],['commands','task receipt','answer'],['Верификация результата и grounded terminal остаются в существующем протоколе.'],
   binding('server/controller_pipeline.py','run_pipeline_worker','process_pipeline_subagents','build_pipeline_task_receipt'))),
 component('multidevice','Multi-device / Broadcast',2,'Разделяет выполнение по контекстам устройств в существующей задаче; не новая шина.',
  mechanism('device-fanout','Device-scoped execution','Определяет broadcast и запускает run_on_device с проверкой владельца; собирает результаты устройств.',
   ['device_ids','broadcast','task'],['device-scoped results'],['Контексты разных устройств не являются взаимозаменяемыми.'],
   binding('server/task_runtime.py','run_nl_task','run_nl_task.run_on_device'),binding('server/controller_pipeline.py','build_pipeline_other_devices_summary'))),
 component('llm','LLM transport / routing',1,'Конфигурируемый chat/completions transport и выбор executor; отдельные provider adapters не подтверждены.',
  mechanism('llm-transport','Chat completion transport','Формирует chat/completions запрос, thinking fields и usage context; применяет существующие retries.',
   ['client','cfg','model','messages','tools'],['completion response'],['Ответ модели не является разрешением на действие; secrets не входят в атлас.'],
   binding('server/controller.py','load_llm_config','_chat_completion_request','_thinking_request_fields')),
  mechanism('llm-route','Controller route selection','Выбирает LLMRouteSpec по modes и формирует аргументы executor.',
   ['modes','runtime'],['LLMRouteSpec','executor kwargs'],[],
   binding('server/controller.py','LLMRouteSpec','_select_llm_route','_build_route_kwargs'))),
 component('registry','Tool Registry',3,'Каталог существующих инструментов, канонические имена и метаданные результатов.',
  mechanism('tool-contracts','Tool contracts / inventory','Канонизирует имена и выдаёт компактные описания зарегистрированных tools.',
   ['name','category'],['canonical name','tool metadata'],['Реестр описывает tools; членство само по себе не разрешает выполнение.'],
   binding('server/tool_registry.py','canonical_tool_name','list_tools'),binding('server/tool_inventory.py')),
  mechanism('tool-result','Result serialization','Формирует компактные summary и поля журнала существующей операции.',
   ['action','result','command metadata'],['tool log entry','summary'],['Сериализация не доказывает достижение цели.'],
   binding('server/tool_registry.py','compact_tool_summary','tool_log_fields','tool_log_entry'))),
 component('device','Device Execution',3,'Серверный command transport и локальный Agent runtime.',
  mechanism('command-transport','Device command transport','Отправляет команду выбранному агенту и принимает результат с identity receipt.',
   ['device_id','action','params','user_id'],['command result','identity receipt'],['Проверяется устройство и его принадлежность; timeout не равен успешному выполнению.'],
   binding('server/task_runtime.py','send_command_to_agent','_attach_identity_receipt')),
  mechanism('agent-dispatch','Agent dispatch','WebSocket runtime принимает command и вызывает зарегистрированный ACTIONS handler.',
   ['command payload','action','params'],['result payload'],['Исполнение происходит в агенте, а не в визуализаторе.'],
   binding('agent/core/runtime.py','AgentRuntime','AgentRuntime._run_async','AgentRuntime._handle_command')),
  mechanism('local-actions','Local actions','Локальные реализации shell/file действий и наблюдения состояния устройства.',
   ['command / path / content','device_id'],['action result / state receipt'],['Реальные действия здесь только описываются; атлас никогда их не вызывает.'],
   binding('agent/core/actions.py','execute_cmd','write_content','refresh_device_state','prepare_runtime'),binding('agent/platforms/windows.py'))),
 component('browser','Browser Bridge',3,'Авторизованная передача browser operations расширению и корреляция результатов.',
  mechanism('browser-operations','Browser operation bridge','Проверяет ownership и params, отправляет operation, принимает correlated result.',
   ['user_id','task_id','device_id','operation','params'],['safe browser result'],['Credential, owned device, effect reservation и cancellation проверяются до результата.'],
   binding('server/browser_bridge.py','execute_browser_action','receive_result','validate_params'),binding('browser_extension/background.js'),binding('browser_extension/content.js')),
  mechanism('browser-credentials','Pairing / credential contract','Выдаёт pairing и проверяет credential владельца устройства.',
   ['owner','device_id','token'],['pairing / authenticated connection'],['Секреты и pairing tokens не извлекаются в атлас.'],
   binding('server/browser_bridge.py','require_owned_device','issue_pairing','authenticate_credential'))),
 component('memory','Memory',4,'Серверные факты пользователя и ограниченный memory context.',
  mechanism('memory-read','User fact reads','Возвращает stats/list фактов только authenticated user.',
   ['tool_name','args','user_id'],['facts or stats'],['Без user_id возвращается error; исторические факты не являются authority.'],
   binding('server/memory_tools.py','run_memory_tool'),binding('server/database.py','get_user_facts'),binding('server/controller_shared.py','build_memory_block')),
  mechanism('memory-authority','Memory write authority','Выводит допустимость memory write из human request в существующем guard.',
   ['human request'],['memory permissions'],['Правила не переносятся в UI атласа; полная модель writes требует дальнейшего обзора.'],
   binding('server/memory_intent_guard.py','memory_permissions_from_human_request'))),
 component('state','State / Persistence',4,'Хранилище task state, сообщений и локального device cache.',
  mechanism('task-state','Task cancellation state','Хранит task state и маркирует запрос остановки и terminal cancellation.',
   ['task_id','task state'],['cancel flags','task status'],[],
   binding('server/runtime_state.py','is_task_cancel_requested','mark_task_cancelled'),binding('server/database.py')),
  mechanism('local-state','Local device state','Хранит локальные snapshots и состояние соединения агента.',
   ['device state / receipts'],['cached state'],[],binding('agent/core/local_state.py'),binding('agent/core/state.py'))),
 component('authorization','Authorization / Confirmation',5,'Идентификация пользователя и привязка подтверждения к pending command.',
  mechanism('token-verification','Access token verification','Проверяет подпись и срок access token.',
   ['access token'],['identity claims or None'],['HMAC secret остаётся вне атласа.'],binding('server/auth.py','verify_access_token')),
  mechanism('command-confirmation','Command confirmation contract','Создаёт confirmation_id, risk kind и разрешённый канал; интерпретирует только command outcome.',
   ['pending command','risk','command result'],['confirmation metadata','command outcome'],['Опасные операции не получают voice authority; успешная команда не доказывает цель задачи.'],
   binding('server/command_confirmation.py','command_confirmation','confirmed_command_outcome'),binding('server/controller_shared.py','ConfirmationRequired'))),
 component('journal','Run Journal',6,'Текущие step IDs, tool results, basis и валидация terminal payload.',
  mechanism('journal-steps','Run step journal','Создаёт и добавляет tool/answer steps; сериализует результаты для модели.',
   ['journal','tool result','answer payload'],['step_id','journal entry','wrapped result'],['Идентификаторы относятся к текущему run; static dependency не означает execution.'],
   binding('server/run_journal.py','make_run_step','append_tool_step','append_answer_step','wrap_tool_result_for_llm')),
  mechanism('terminal-validation','Terminal protocol / basis','Проверяет cardinality tools, basis и answer.text payload.',
   ['tool calls','answer payload','current journal'],['validated payload or ProtocolValidationError'],['Basis не может ссылаться на старый run или отрицательные результаты.'],
   binding('server/run_journal.py','validate_tool_call_batch','validate_basis_references','validate_answer_text_payload')),
  mechanism('diagnostic-journal','Lifecycle diagnostics','Записывает allowlisted lifecycle labels без сериализации request/args/result text.',
   ['event','allowed metadata'],['bounded diagnostic_trace'],['Диагностика не меняет исполнение; metadata не является полной трассой.'],
   binding('server/run_journal.py','diagnostic_context_for_task','record_lifecycle_event'))),
 component('auditor','Answer Auditor',6,'Проверяет терминальный ответ относительно текущих доказательств и исходного user request.',
  mechanism('answer-audit','Grounded answer audit','Передаёт compact journal и payload аудитору; возвращает verdict/reason/availability.',
   ['user_request','current_run_journal','answer_payload'],['bool verdict','reason','availability'],['Self-check labels и returncode=0 не заменяют доказательство цели; auditor может быть отключён конфигом.'],
   binding('server/answer_auditor.py','audit_answer_payload','_compact_journal'),binding('server/controller_trust.py','has_grounded_terminal_answer'))),
 component('report','Worker Report',6,'Строгая outcome projection существующих receipts и evidence, не произвольная LLM prose.',
  mechanism('report-projection','Outcome report projection','Собирает WorkerReport из команд, basis, task receipt и status.',
   ['task','commands','task_receipt'],['schema-v1 WorkerReport'],['goal_completed согласован с success; требуется evidence_refs.'],
   binding('server/worker_reports.py','WorkerReport','build_worker_report','positive_evidence'),binding('server/worker_scheduler.py','persist_report'))),
 component('presentation','Response Presentation',6,'Сохраняет raw protocol text и строит разговорное представление из проверенного отчёта.',
  mechanism('result-presentation','Result presentation','Нормализует report, отбирает verified artifacts и строит conversational_response/execution_details.',
   ['task','WorkerReport','commands','artifacts'],['conversational_response','execution_details'],['Только verified artifacts разрешённых devices; protocol answer не переписывается.'],
   binding('server/response_presentation.py','normalized_worker_report','worker_presentation','_artifacts','_artifact_result'))),
 component('webui','Web UI / Smart UI',0,'Отображает чат, задачи и Operations на основе server snapshots.',
  mechanism('chat-ui','Chat rendering / polling','Отправка сообщений, polling task и отображение истории реализованы существующим Web UI.',
   ['chat messages','task snapshots'],['chat DOM','task details'],['Браузерный DOM не является серверным evidence journal.'],binding('ui/js/chat.js'),binding('ui/js/smart-ui.js'),binding('ui/js/operations.js'))),
 component('voice','Voice',0,'Voice session управляет вводом/воспроизведением; сервер готовит spoken parts и синтез.',
  mechanism('voice-session','Voice session control','Сессия управляет listening, working и playback; сервер выбирает spoken_parts.',
   ['voice session events','task answer'],['spoken parts','session phase'],['История, tools и authority остаются в IRU; Voice не отдельный executor.'],
   binding('ui/js/voice-session.js'),binding('ui/js/voice.js'),binding('server/voice.py','spoken_parts','synthesize')))
]

UNRESOLVED = [
 {'area':'Provider adapters','reason':'Подтверждён общий transport и config routing; отдельных adapter компонентов не установлено.'},
 {'area':'JavaScript internals','reason':'Модули просмотрены; автоматический AST JS функций отсутствует.'},
 {'area':'Dynamic dispatch / event graph','reason':'Callback executor, ACTIONS и event dispatch не равны статически доказанным конкретным runtime-вызовам.'}
]

# Snapshot 2026-10-23: pipeline removed; Goal DAG and native tools added.
CATALOG=[c for c in CATALOG if c['id']!='component:plan']
for c in CATALOG:
 for m in c['mechanisms']:
  m['bindings']=[b for b in m['bindings'] if b['path']!='server/controller_pipeline.py']
 c['mechanisms']=[m for m in c['mechanisms'] if m['bindings']]

def extend(key,*mechanisms):
 next(c for c in CATALOG if c['id']=='component:'+key)['mechanisms'].extend(mechanisms)

extend('scheduler',
 mechanism('goal-admission','Goal admission / revisions','Транзакционно принимает задачи общей цели, проверяет ревизию и зависимости, связывает заменяющие результаты.',
 ['assignments','goal_id','goal_revision','owner/chat'],['worker_goals','worker_goal_tasks','worker_dependencies'],['DAG проверяется до записи; цель и исходные результаты принадлежат владельцу и чату.'],
 binding('server/worker_scheduler.py','validate_task_graph','WorkerScheduler.submit_goal','WorkerScheduler.amend_pending')),
 mechanism('pinned-results','Pinned result dependencies','Закрепляет результат с SHA256 и передаёт проверенные зависимости следующему заданию.',
 ['task report','dependency tasks'],['worker_results','pinned_results'],['Доступ ограничен owner/chat/device; результат не заменяется произвольным текстом.'],
 binding('server/worker_scheduler.py','_freeze_result','pinned_results','_final_snapshot')))
CATALOG.append(component('goals','Goals / Task DAG',2,'Общая цель состоит из зависимых задач и ревизий; итог выводится из всех требуемых результатов.',
 mechanism('goal-outcome','Goal outcome / superseded tasks','Строит итог цели по актуальным участникам, исключает заменённые задания и проверяет закреплённый результат.',
 ['goal_id','owner','chat_id'],['goal status','tasks','goal_completed'],['Один успешный Task не означает успех всей цели.'],binding('server/worker_scheduler.py','goal_view','WorkerScheduler.cancel_group')),
 mechanism('goal-presentation','Goal result presentation','Формирует представление цели, файлов, прогресса и заменённых результатов для интерфейса.',
 ['goal view'],['goal presentation'],['Отображение не создаёт доказательств успеха.'],binding('server/goal_presentation.py','goal_presentation'),binding('ui/js/mini-operations.js'))))
CATALOG.append(component('office','Native Office tools',3,'Локальные операции DOCX/XLSX/PPTX через нативные библиотеки, без управления Office UI.',
 mechanism('office-contract','Office tool schemas','Объявляет схемы и проверяет аргументы Office tools.', ['tool arguments'],['validated operation'],['Неизвестные параметры и неподдерживаемые операции отклоняются.'],binding('server/office_tool_schemas.py','office_schemas','office_metadata','validate_office_args')),
 mechanism('office-operations','DOCX / XLSX / PPTX operations','Читает, создаёт и изменяет документы с проверками структуры, отпечатка и сохранности содержимого.',
 ['path','action','data','changes','expected_sha256'],['document result','artifact receipt'],['Изменение пакета и итоговый артефакт проверяются отдельно от принятия tool call.'],binding('agent/core/office_tools.py','office','docx_operation','xlsx_operation','pptx_operation','safe_edit_package','verify_preservation'))))
CATALOG.append(component('artifacts','Verified artifacts / write transport',6,'Связывает запись файла, проверенный артефакт и доставку файла с исходным заданием/устройством.',
 mechanism('chunked-writes','Bounded write transport','Разбивает одну логическую запись на последовательные части с digest и финализацией.', ['content','path'],['write session','final write result'],['Частичная передача не равна завершённой записи.'],binding('server/write_transport.py','write_in_chunks'),binding('agent/core/actions.py','_write_content','write_content')),
 mechanism('artifact-verification','Artifact verification','Проверяет фактический файл и возвращает metadata, привязанные к исходному заданию.', ['path','source task'],['verified artifact'],['Запуск и stdout не заменяют проверку файла.'],binding('agent/core/actions.py','_verify_artifact')),
 mechanism('file-transfer','File transfer authorization','Передаёт файлы через временный транспорт с owner/device проверками и управлением статусом передачи.', ['owner','run_id','transfer args'],['transfer result'],['Доступ к transfer ограничен токеном/назначением/состоянием; это не публичная файловая раздача.'],binding('server/file_transfer.py','transfer_file','authorize','require_device','download'))))
extend('authorization',
 mechanism('execution-scope','Execution scope / window input','Разделяет general/window scope, запрашивает ввод пользователя для неоднозначного окна и продолжает именно ожидающее задание.',
 ['execution_scope','effects','window options'],['validated scope','pending window input'],['Оконный scope не расширяет полномочия general.'],binding('server/worker_scheduler.py','validated_execution_scope','WorkerScheduler.answer_window_input'),binding('server/window_policy.py','window_input_required','window_input_choice'),binding('server/task_runtime.py','continue_window_input')),
 mechanism('memory-authorization','Current-turn memory authorization','Представляет явное разрешение на сохранение фактов из текущей реплики в контракте маршрутизатора.', ['current human turn'],['MemoryAuthorization'],['История не выдаёт новое разрешение на запись памяти.'],binding('server/orchestrator.py','MemoryAuthorization','Decision')))
extend('webui',mechanism('ui-modes','Desktop / mini / mobile surfaces','Разделяет оформление режимов и отображает Goal, файлы и ожидающие подтверждения.',
 ['task/goal presentation','pending confirmation'],['mode-specific UI'],['UI отображает серверное состояние, не выполняет shell-команды из summary.'],binding('ui/js/operations.js'),binding('ui/js/mini-operations.js'),binding('ui/css/modes/shared/workspace.03.css'),binding('ui/css/modes/mobile/product-v2.02.css')))
for c in CATALOG:
 if c['id']=='component:orchestrator':
  c['responsibility']=c['description']='Маршрутизирует текущую реплику через Decision и assignments; контекст отделён от текущего ввода, повторяющиеся инструкции удаляются.'
 if c['id']=='component:multidevice':
  c['responsibility']=c['description']='Выполнение на разрешённых устройствах через Task runtime; старый pipeline fanout удалён.'

extend('context',mechanism('memory-intent-guard','Memory intent guard','Проверяет разрешённость фактов памяти до выполнения соответствующего инструмента.', ['tool arguments','current authorization'],['guard decision'],['Сохранение памяти требует разрешения текущей реплики.'],binding('server/memory_intent_guard.py')))
extend('worker',mechanism('controller-budget','Controller recovery / budget','Ограничивает бюджет итераций и поддерживает восстановление наблюдений без признания неизвестного успехом.', ['run context','tool results'],['budget state','recovery observations'],['Повторная попытка не является доказательством достижения цели.'],binding('server/controller_budget.py'),binding('server/controller_trust.py')))
for c in CATALOG:
 for m in c['mechanisms']:
  if m['id']=='mechanism:turn-handoff':m['outputs']=['Decision response','Goal/Task admission','task status or cancellation']
  if m['id']=='mechanism:device-fanout':m['description']=m['responsibility']='Последовательное выполнение задания на разрешённых устройствах через task runtime, без удалённого pipeline executor.'
# Explicit file membership of the frontend mode layers; not a JS call-graph claim.
extend('webui',mechanism('frontend-styles','Frontend mode styles','CSS-слои desktop/mini/mobile разделяют оформление общих поверхностей.', ['mode stylesheets'],['visual presentation'],['Только оформление; нет влияния на статус исполнения.'],
 binding('ui/css/base.css'),binding('ui/css/mobile.css'),binding('ui/css/workspace.css'),binding('ui/css/product-v2.css'),binding('ui/css/surfaces.css')))

extend('office',mechanism('office-preservation','Office content preservation','Сравнивает содержимое документа до и после форматирования; повторно открывает сохранённый пакет.', ['original package','reopened package'],['preservation result'],['Флаг сохранности требует сравнения содержимого, а не только успешного save.'],binding('agent/core/office_content.py'),binding('agent/core/office_tools.py','verify_preservation')))
extend('voice',mechanism('voice-provider','Voice provider / Piper','Серверный голосовой слой выбирает provider; Piper получает оформление ударений для spoken_response.', ['voice session','spoken_response'],['speech audio'],['Озвучка не меняет письменный итог и не даёт разрешения на исполнение.'],binding('server/voice.py'),binding('server/orchestrator.py','decide'),binding('agent/core/voice.py')))
