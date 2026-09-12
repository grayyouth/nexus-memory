# 🗺️ Мастер-план Nexus

> Nexus — универсальная система памяти для AI-агентов (Cline, Gea, локальные LLM).
> Цель: превратить набор скриптов в полноценный продукт, который решает проблему
> **межсессионной памяти** и **удобного доступа к знаниям** для любых агентов.

**Легенда статусов:**
- ✅ **Реализовано**
- 🚧 **В работе / частично**
- ⏳ **Запланировано**

**Версия:** 0.9.8 · **Обновлено:** 2026-09-12

---

## 📌 Актуальный режим работы (с 2026-09-05)

Проект перешёл в **эксплуатацию и наполнение параллельно с доработкой**:

1. Каждая сессия агента начинается с `get_context(agent_id, project_id)` — «загрузка памяти».
2. Важные находки сохраняются через `add_note` / `record_joint_decision`.
3. В конце сессии — `archive_current_session` + `store_session_summary`.
4. Договорённость зафиксирована в самой памяти Nexus и в `AGENT_INSTRUCTIONS.md`.

> Полный ритуал для агентов — в [AGENT_INSTRUCTIONS.md](./AGENT_INSTRUCTIONS.md).

---

## Этап 1 — Базовое ядро ✅

Готовый фундамент, на котором строится всё остальное.

| Задача | Статус | Комментарий |
| :--- | :---: | :--- |
| Структура хранилища `nexus_store/` | ✅ | `input_docs`, `main_library`, `sessions`, `_index`, `_logs` |
| Ядро `core/nexus_core.py` (класс `Nexus`) | ✅ | `add_chunk`, `search`, `archive_session`, `store_session_summary`, `get_context_for_agent`, `add_joint_decision` |
| MCP-сервер `nexus_mcp_server.py` | ✅ | SDK `mcp>=2.1`, `MCPServer`, 7 инструментов |
| Конфиг подключения `nexus_mcp_config.json` | ✅ | stdio-транспорт, `python nexus_mcp_server.py` |
| Инструкция `README.md` | ✅ | Описание, сценарии работы, интеграция |
| Регламент агентов `AGENT_INSTRUCTIONS.md` | ✅ | Ритуал сессии: `get_context` → `add_note` → `archive` + `summary` |
| Договорённость записана в память Nexus | ✅ | `record_joint_decision` + `add_note` (#решение #сопроцессор #договорённость) |

---

## Этап 2 — Ingestion Pipeline ✅

Автоматическая обработка сырых документов → структурированная библиотека.

| Задача | Статус | Комментарий |
| :--- | :---: | :--- |
| Модуль `core/ingestion.py` | ✅ | `raw/` → извлечение → нормализация → чанки → индекс → `processed/` |
| Поддержка форматов | ✅ | `.md/.txt/.json/.jsonl/.csv/.html` |
| Front-matter из `.md` | ✅ | `tags`, `project`, `source` → метаданные чанка |
| Семантическое чанкование | ✅ | По заголовкам, лимит ~1200 символов (дружелюбно к малым контекстам) |
| Логирование | ✅ | `nexus_store/_logs/ingestion.jsonl` |
| MCP: `run_ingestion` / `get_ingestion_status` | ✅ | Агент может запустить пайплайн из сессии |

---

## Этап 3 — Межсессионная память агентов ✅

Решение главной проблемы: **агент должен «вспоминать» прошлые сессии**.

| Задача | Статус | Комментарий |
| :--- | :---: | :--- |
| Архивация сырых сессий по `agent_id` | ✅ | `archive_session` → `sessions/archived_sessions/agent_<id>/`; уникальность имён при нескольких архивах в одну минуту |
| Сводки сессий (`store_session_summary`) | ✅ | Механизм готов; ручной режим — как запасной вариант |
| ПЕРВАЯ сводка истории разработки (agent=cline) | ✅ | Записана как «память о прошлых сессиях» (отдаётся в `get_context`) |
| Загрузка контекста новой сессии | ✅ | `get_context_for_agent`: личная сводка + совместные знания проекта; fallback на свернутое резюме |
| **Автогенерация сводок** из архива | ✅ | `core/summarizer.py` (`SessionSummarizer`): эвристика RU/EN-ключей → *Сделано/Решения/Дальше*, без LLM; MCP `generate_session_summary` |
| Сворачивание старых архивов | ✅ | `collapse_history`: старые сессии → 1 строка каждая, агрегация решений; поле `collapsed_summary` в индексе; MCP `collapse_session_history` |
| Автоархивация при завершении сессии | ✅ | `core/session_hook.py` (`SessionHook.end_session`): архив + автосводка (+ опционально collapse) одним вызовом; MCP `end_session` |

---

## Этап 4 — Поиск и работа с локальными LLM ✅

Ключевое требование: **локальные модели с маленьким контекстом и медленной генерацией**.

| Задача | Статус | Комментарий |
| :--- | :---: | :--- |
| Ключевой поиск (`search`) | ✅ | Словарное совпадение + фильтры тегов/проекта/агента |
| **Семантический поиск** (эмбеддинги) | ✅ | `core/semantic.py`: HashingEmbedder 512d (zero-dep, RU/EN, детерминированный md5), автопереключение на sentence-transformers при установке; персистентный индекс `_index/vectors.json`; гибрид: косинус + keyword-бонус; MCP `semantic_search`. Ограничение zero-dep: без RU↔EN кросс-языка |
| **Промпты-инъекции** | ✅ | `build_prompt_block` / MCP `build_context_prompt`: готовый блок контекста «под задачу» из топ-чанков |
| Компрессия контекста | ✅ | Топ-K чанков с жёстким бюджетом символов (`max_chunks`/`max_chars`) вместо «прочитай всё» |
| Кэш результатов поиска | ✅ | In-process кэш с инвалидацией по версии индекса (до 128 записей) |
| **Оценка доверия к источнику** | ✅ | `core/trust.py`: trust 0..1 по источнику (официальные домены/docs./*.gov/*.edu → 1.0, локальные доки → 0.8, неизвестный URL → 0.4); поле `trust` в чанках; семантический score демпфируется `(1 - w + w*trust)`, `trust_weight=0.25` в `nexus_config.json` |

---

## Этап 5 — Совместная работа агентов ✅

Общая память при решении одной задачи несколькими агентами.

| Задача | Статус | Комментарий |
| :--- | :---: | :--- |
| `record_joint_decision` | ✅ | Решение + список агентов + причина |
| Хранение в `sessions/cross_sessions/collab_<project>/` | ✅ | Общий доступ всех участников проекта |
| Изоляция личных знаний по `agent_id` | ✅ | Личное ≠ общее; cline не видит личные заметки Gea |

---

## Этап 6 — Расширение форматов документов ✅

| Задача | Статус | Комментарий |
| :--- | :---: | :--- |
| **PDF (`pypdf` / `PyMuPDF`)** | ✅ | Извлечение текста из `.pdf`; pypdf (pure Python) + fallback на PyMuPDF (fitz) |
| **OCR изображений** (`pytesseract` / `easyocr`) | ✅ | Текст из `.png/.jpg/.jpeg/.bmp/.tiff/.webp`; fallback между бэкендами; auto-select |
| **Веб-страницы по URL** | ✅ | `core/web.py`: скачивание HTML → `raw/` → пайплайн; content extraction (strip nav/script/style) |
| **EPUB / DOCX** | ✅ | DOCX: zero-dep zipfile+XML (python-docx fallback); EPUB: container.xml → OPF → spine → XHTML (ebooklib fallback) |

---

## Этап 7 — Автоматизация и продукт ✅

| Задача | Статус | Комментарий |
| :--- | :---: | :--- |
| **Тесты (`pytest`)** | ✅ | 257 тестов в 11 файлах, 7.3s — покрыты все модули |
| **CLI-утилита `nexus`** | ✅ | `add`, `search`, `ingest`, `context`, `summary`, `decisions`, `watch`, `config`, `web`, `orch` |
| **Watchkeeper (авто-ingestion)** | ✅ | `core/watchkeeper.py`: сканирует `raw/` каждые N сек; MCP + CLI |
| **pip-упаковка (`pyproject.toml`)** | ✅ | `pip install -e ".[ocr]"`, `nexus` CLI из командной строки, optional deps |
| **Конфиг пользователя** | ✅ | `nexus_config.json`: пути, лимиты, OCR, auto-start; MCP + CLI `config get/set` |
| **Веб-страницы по URL** | ✅ | `core/web.py`: скачивание HTML → `raw/` → пайплайн; content extraction |
| **Интеграция с оркестратором** | ✅ | `core/orchestrator.py`: start/end/cancel tasks, agent tracking, project stats |
| Периодический запуск ingestion (таймер/наблюдатель) | ✅ | watchkeeper: сканирует `raw/` каждые N минут |
| Упаковка (pip / uv) | ✅ | `pyproject.toml`, `pip install nexus-memory`, `nexus` CLI |

---

## Этап 8 — Кросс-агентный дайджест проекта ✅

«Что изменилось в проекте, пока я отсутствовал» — единый блок для любого агента:
сессии всех агентов (архивированные с `project_id`) + совместные решения +
свежие заметки проекта, с фильтром `since`.

| Задача | Статус | Комментарий |
| :--- | :---: | :--- |
| `archive_session` / `end_session` / HTTP / MCP: опциональный `project_id` | ✅ | Сессии тегаются проектом → видны всем агентам проекта |
| Модуль `core/project_digest.py` (`ProjectDigest`) | ✅ | Эвристика без LLM, переиспользует `SessionSummarizer.extract_structure`; тюнинг через `nexus_config.json → digest.*` |
| Фильтр `since` (ts сессии / ISO / дата) | ✅ | «С N-го момента» |
| Подключение в `get_context(agent_id, project_id)` | ✅ | Автоматический дайджест чужих изменений со времени последней сессии агента |
| CLI `nexus digest --project X [--since …] [--days N]` | ✅ | Плюс `--from-agent` / `--exclude-agent` |
| MCP `project_digest` + HTTP `/mcp/project_digest` | ✅ | |
| Тесты | ✅ | `tests/test_project_digest.py` |
| Legacy-ограничение | ⚠️ | Сессии, заархивированные БЕЗ `project_id`, проекту не приписываются (ретро-тег не делается) |

---

## Этап 9 — Безопасная запись + сжатие сессий 🚧

Старт реализации раздела **необходимых разработок** (чанк `e5c24fc8`) и
сжатия сессий (чанк `c7464edb`), согласованных 2026-09-11.

| Задача | Статус | Комментарий |
| :--- | :---: | :--- |
| **Фаза 1: атомарные записи (temp + rename)** | ✅ | `core/safe_io.py`: `atomic_write_json/text` + `os.replace`; ни один читатель не видит полузаписанный файл |
| **Фаза 1: файловый лок на индексы** | ✅ | `core/safe_io.py`: кроссплатформенный лок (`msvcrt`/`fcntl`/lock-file fallback + process-local guard для потоков одного процесса); `Nexus._update_json` — локализованный read-modify-write |
| Переведены на безопасные записи | ✅ | `add_chunk` (chunks+tags), `archive_session`, `store_session_summary`, `add_joint_decision`, `generate_session_summary`, `collapse_history`, `orchestrator._save_tasks`, `config.save` |
| Конкурентная целостность | ✅ | Тест: 6 потоков `update_json_file` → ровно 6 инкрементов без потерь; кросс-процессный конфликт лока даёт `LockTimeout` |
| **Сжатие сессий (`compress_session`)** | ✅ | `SessionSummarizer.compress_session` + standalone-функция; уровни 1 (эвристика) / 2 (LLM-апгрейд через `llm_call` с graceful fallback); бюджет `max_chars`; markdown-блок для `get_context` |
| CLI `nexus compress` | ✅ | `--agent / --session-id / --raw-file / --level / --max-chars` |
| MCP `compress_session` + HTTP `/mcp/compress_session` | ✅ | |
| Конфиг `compressor.*` | ✅ | `default_level`, `max_done/decisions/next/other`, `max_dialog_*`, `max_chars` |
| Тесты | ✅ | `tests/test_safe_io.py` (14) + `tests/test_compressor.py` (13) |
| **Фаза 2: серверный режим (FastAPI-демон)** | ✅ | `nexus_http_server.py`: `create_app()`-фабрика, токен-middleware (Bearer; `/healthz` публичный), `main()` c argparse/pid-file/лог-файлом, фоновые `_autoclose_loop()` + watchkeeper; `core/autoclose.py` — backfill сводок по агентам; `core/server_client.py` — `NexusClient` (generic `call()` + ~20 typed-методов, `client_from_config()`); MCP `session_autoclose` + daemon-mode proxy (`server.mode='daemon'` → каждый тул делегирует на `POST /mcp/<tool>` демона); тесты `test_autoclose.py`, `test_server_client.py`, `test_http_server.py`, `test_mcp_daemon.py` |
| **Фаза 3: `nexus server start|stop|status` + автозапуск** | ✅ | CLI `nexus server start/stop/status/token` (detached-процесс, pid-file, автогенерация токена, лог) + общий `nexus status`; автозапуск при входе в систему — `nexus server autostart enable|disable|status` (`core/autostart.py`: Windows schtasks ONLOGON → fallback HKCU Run-ключ (без админа) / Linux systemd user unit → fallback cron `@reboot` / macOS LaunchAgent; config `server.auto_start`; тесты `test_autostart.py`) |
| **Автозакрытие сессий по таймауту** | ✅ | `auto_close_stale_sessions` — фоновый тред демона (`_autoclose_loop`) + MCP-тул `session_autoclose` |

---

## Бэклог (идеи)

> Раздел **необходимых разработок** (согласован) сохранён в Nexus: чанк `e5c24fc8`
> (#roadmap #development, project `Nexus`) — автономный сервер-процесс (фазы 1–3),
> автоматизация (auto end_session по таймауту, авто-проект/теги, LLM-сводки,
> replication на D:), новый функционал (BM25/RU-стеминг, экспорт/импорт, веб-дашборд,
> event feed → мост к NeoKron, граф знаний, мульти-стор).
> Фаза 1 (atomic + lock) — реализована в Этапе 9.
>
> **НЕОБХОДИМЫЕ РАЗРАБОТКИ (доп.) — сжатие сессий**: чанк `c7464edb`
> (#compression): переиспользуемый механизм «компакт» длинных сессий (по образцу
> встроенного в opencode) для Nexus-архивов/NeoKron/Мнемозины; база — эвристика
> SessionSummarizer/extract_structure + опциональный LLM-апгрейд (llama-server);
> цель — сохранять факты/решения/задачи при резком сокращении токенов.
>
> **Статус на 2026-09-12:** Фаза 1 (atomic+lock) ✅, `compress_session` ✅,
> Фаза 2 (FastAPI-демон + MCP daemon-mode proxy) ✅, `nexus server` CLI ✅
> (автозапуск демона при старте системы — 🚧), автозакрытие сессий по
> таймауту ✅ (фоновый тред демона + MCP `session_autoclose`).
> Дальше: автозапуск демона при старте системы, авто-проект/теги при ингесте,
> LLM-сводки (интеграция llama-server в `compress_session(level=2)`).

- **Память «рабочих цепочек»** — запоминание успешных последовательностей действий («как мы решали похожую задачу в прошлый раз»).
- **Граф знаний** — связи между чанками (тема → решение → проблема), вместо плоского списка.
- **Sessions API для человека** — веб-интерфейс / дашборд по библиотеке.
- **Мульти-контекстные сводки** — разные резюме одной сессии под разные задачи (код / решения / ошибки).

---

## Правила работы с роадмапом

1. Каждое изменение — отдельный PR/коммит с пунктом из плана.
2. Статус меняется только при реально работающем коде + проверке.
3. Приоритет — задачи Справа налево в порядке: **локальные LLM → сессии → форматы → продукт**.