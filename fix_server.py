
import re

with open('E:/VSCodeProjects/Nexus/nexus_http_server.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace all POST endpoints to use Request
# Pattern: @app.post(...)\nasync def func(params):\n    body
# Replace with: async def func(req: Request):\n    data = await req.json()\n    body

endpoints = [
    ('search_knowledge', 'query, tags, project_id, agent_id'),
    ('add_note', 'content, tags, project_id, source, agent_id'),
    ('archive_current_session', 'agent_id, session_data'),
    ('get_context', 'agent_id, project_id'),
    ('record_joint_decision', 'project_id, decision, by_agents, reason'),
    ('run_ingestion', 'max_files'),
    ('generate_session_summary', 'agent_id, session_id'),
    ('collapse_session_history', 'agent_id, keep_last'),
    ('end_session', 'agent_id, session_data, collapse_after, keep_last'),
    ('semantic_search', 'query, top_k, tags, project_id, agent_id'),
    ('build_context_prompt', 'query, project_id, agent_id, max_chunks, max_chars'),
    ('start_watchkeeper', 'interval'),
    ('stop_watchkeeper', ''),
    ('watch_status', ''),
    ('ocr_scan_images', 'directory, engine, languages'),
    ('ocr_extract_image', 'image_path, engine, languages'),
    ('ocr_status', ''),
    ('config_get', ''),
    ('config_set', 'section, key, value'),
    ('config_update', 'settings'),
    ('web_fetch', 'url, extract_content, timeout'),
    ('web_save', 'url, project_id, timeout'),
    ('web_status', ''),
    ('orch_start_task', 'task_id, agent_id, project_id, description, model, variant'),
    ('orch_end_task', 'task_id, agent_id, session_data'),
    ('orch_status', ''),
    ('orch_list_tasks', 'agent_id, project_id, status'),
]

for name, params in endpoints:
    # Match the function signature
    pattern = rf'async def {name}\(([^)]+)\):'
    match = re.search(pattern, content)
    if match:
        old_sig = match.group(0)
        if params:
            new_sig = f'async def {name}(req: Request):'
            content = content.replace(old_sig, new_sig)
        # else: no params, keep as is

with open('E:/VSCodeProjects/Nexus/nexus_http_server.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed signatures')
