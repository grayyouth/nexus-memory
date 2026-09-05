# 🧠 Nexus — Универсальная память для AI-агентов

**Nexus** — это система общей памяти для любых AI-агентов (Cline, Gigachat, локальные LLM
и другие). Она решает главную проблему: **агенты не помнят, что делали в прошлых сессиях**.
Nexus хранит знания, сессии и совместные решения в прозрачном формате файлов и отдаёт их
агенту по запросу через MCP.

> Философия: Nexus — это «процессор», а не «заместитель мышления». Агент принимает решения,
> Nexus хранит, индексирует и быстро отдаёт нужную информацию.

---

## 📦 Установка

### pip (рекомендуется)

```bash
# Базовая установка
pip install -e .

# С поддержкой OCR
pip install -e ".[ocr]"

# Полная установка (включая dev-зависимости)
pip install -e ".[all]"
```

### Из исходников

```bash
git clone <repo>
cd Nexus
pip install -e ".[all]"
```

### Ручной режим (без pip)

```bash
# Клонируйте репозиторий и добавьте в PYTHONPATH
export PYTHONPATH="$PYTHONPATH:$(pwd)"

# Или запустите напрямую из корня проекта
cd e:\VSCodeProjects\Nexus
python nexus_cli.py add "текст" --tags "#test"
```

### Зависимости

| Пакет | Назначение | Обязательно |
| :--- | :--- | :---: |
| `mcp>=2.1` | MCP-сервер для подключения агентов | ✅ |
| `pypdf` | Извлечение текста из PDF | ✅ |
| `pytesseract` | OCR изображений (быстрый) | ⏳ |
| `easyocr` | OCR изображений (качественный) | ⏳ |
| `pytest` | Тесты | ❌ |

> Для OCR: `pytesseract` требует установленный в системе Tesseract (`apt install tesseract-ocr` / `choco install tesseract`).
> `easyocr` работает из коробки, но тяжелее.

---

## ⚙️ Конфигурация

Nexus использует `nexus_config.json` для хранения настроек.

### Структура конфига

```json
{
  "version": "0.9.3",
  "store": { "base_dir": null },
  "ingestion": { "max_chunk_chars": 1200 },
  "watchkeeper": { "default_interval": 300, "auto_start": false },
  "summarizer": { "max_items_per_bucket": 10, "max_item_chars": 300, "max_dialog_messages": 5 },
  "semantic": { "vector_size": 512, "cache_size": 128, "use_sentence_transformers": false },
  "ocr": { "default_engine": null, "default_languages": ["eng", "rus"] },
  "prompt_templates": { "context_prompt_max_chunks": 5, "context_prompt_max_chars": 1800 }
}
```

### CLI

```bash
# Показать текущий конфиг
python nexus_cli.py config get

# Показать сырой JSON
python nexus_cli.py config show

# Обновить значение
python nexus_cli.py config set --section watchkeeper --key default_interval --value 600
```

### MCP

```
config_get()                # показать текущие настройки
config_set(section, key, value)  # обновить одно значение
config_update(settings)     # обновить несколько значений
```

### Ключевые настройки

| Настройка | По умолчанию | Описание |
| :--- | :---: | :--- |
| `store.base_dir` | `./nexus_store` | Путь к хранилищу (null = default) |
| `ingestion.max_chunk_chars` | 1200 | Максимальный размер чанка |
| `watchkeeper.default_interval` | 300 | Интервал сканирования raw/ (сек) |
| `watchkeeper.auto_start` | false | Автозапуск watchkeeper при старте |
| `ocr.default_languages` | `["eng", "rus"]` | Языки OCR по умолчанию |
| `ocr.default_engine` | null | Движок OCR (null = auto) |
| `semantic.use_sentence_transformers` | false | Использовать нейросетевые эмбеддинги |

---

## 🌐 Веб-страницы

Nexus умеет скачивать веб-страницы и сохранять их для автоматической индексации.

### CLI

```bash
# Скачать и показать контент
python nexus_cli.py web fetch "https://example.com/docs"

# Скачать и сохранить в raw/
python nexus_cli.py web save "https://example.com/docs" --project "MyProject"

# Показать статус
python nexus_cli.py web status
```

### MCP

```
web_fetch(url)              # скачать и вернуть контент
web_save(url, project_id?)  # скачать и сохранить в raw/ для индексации
web_status()                # доступные способы скачивания
```

### Как это работает

1. `web_save` скачивает HTML-страницу
2. Извлекает основной контент (удаляет nav, script, style, footer)
3. Сохраняет в `input_docs/raw/` с front-matter метаданными
4. Watchkeeper (или `run_ingestion`) автоматически обрабатывает файл

---

## 🤖 Оркестратор

Nexus управляет задачами multi-agent оркестратора:

### CLI

```bash
# Запустить задачу
python nexus_cli.py orch start --task-id task_001 --agent cline --project MyProject --description "Fix login"

# Завершить задачу
python nexus_cli.py orch end --task-id task_001 --agent cline

# Показать статус
python nexus_cli.py orch status

# Список задач
python nexus_cli.py orch list --agent cline --project MyProject

# Очистить все задачи
python nexus_cli.py orch clear
```

### MCP

```
orch_start_task(task_id, agent_id, project_id)  # запустить задачу
orch_end_task(task_id, agent_id, session_data?) # завершить задачу
orch_status()                                    # статус оркестратора
orch_list_tasks(agent_id?, project_id?, status?) # список задач
```

### Как это работает

1. `orch_start_task` загружает контекст из Nexus для агента и проекта
2. Задача отслеживается с статусом `running`
3. `orch_end_task` сохраняет сессию в Nexus (archive + summary)
4. Статистика: задачи по агентам, проектам, статусам

---

## 📁 Структура хранилища

```
nexus_store/
├── input_docs/          # Сырые входящие документы (до обработки)
│   ├── raw/             #   необработанные файлы (text, pdf, md, json...)
│   └── processed/       #   обработанные файлы
├── main_library/        # Главная библиотека структурированных знаний
│   ├── projects/<id>/   #   знания по проектам
│   ├── general/         #   общие знания (ссылки, промты, паттерны)
│   └── _index/          #   служебные индексы (chunks_index.json, tags.json)
└── sessions/            # Сессии агентов
    ├── archived_sessions/   # сырые архивы сессий (по agent_id)
    ├── session_summaries/   # сводки сессий (по agent_id)
    ├── cross_sessions/      # совместные знания по проектам
    └── index.json           # индекс сессий
```

---

## 🔌 Как подключиться (MCP)

Сервер: **`e:\VSCodeProjects\Nexus\nexus_mcp_server.py`**

Пример конфигурации для клиентов MCP (Cline, VS Code и др.) — файл
[`nexus_mcp_config.json`](./nexus_mcp_config.json):

```json
{
  "mcpServers": {
    "nexus": {
      "command": "python",
      "args": ["E:/VSCodeProjects/Nexus/nexus_mcp_server.py"],
      "env": {}
    }
  }
}
```

Проверка подключения из командной строки:

```bash
cd e:\VSCodeProjects\Nexus
python nexus_mcp_server.py
```

Сервер ждёт сообщений по протоколу MCP (stdio) и молчит до запроса — это нормально.

---

## 🛠️ MCP-инструменты (Tools)

| Инструмент | Назначение |
| :--- | :--- |
| `search_knowledge(query, tags?, project_id?, agent_id?)` | Найти знания по ключевым словам с фильтрами. |
| `add_note(content, tags, project_id?, source?, agent_id?)` | Сохранить новое знание в библиотеку. |
| `get_context(agent_id, project_id?)` | Получить контекст для новой сессии: сводку прошлой + совместные решения. |
| `archive_current_session(agent_id, session_data)` | Сохранить сырые данные текущей сессии в архив. |
| `record_joint_decision(project_id, decision, by_agents, reason)` | Записать решение, принятое несколькими агентами. |
| `run_ingestion(max_files?)` | Запустить пайплайн обработки `input_docs/raw/` (форматы md/txt/json/jsonl/csv/html/pdf/png/jpg/bmp/tiff/webp). |
| `get_ingestion_status()` | Показать последние записи лога ингестии. |
| `generate_session_summary(agent_id, session_id?)` | Автосводка архивной сессии в формате *Сделано / Решения / Дальше* (эвристика, без LLM). |
| `collapse_session_history(agent_id, keep_last=1)` | Свернуть старые архивы сессий в одно резюме истории (экономия места и токенов). |
| `end_session(agent_id, session_data, collapse_after?, keep_last=1)` | Завершить сессию одним вызовом: архив + автосводка (+ опционально свернуть старые архивы). |
| `semantic_search(query, top_k?, tags?, project_id?, agent_id?)` | Поиск по смыслу (векторы, гибрид с ключевым совпадением), RU/EN, без внешних сервисов. |
| `build_context_prompt(query, project_id?, agent_id?, max_chunks=5, max_chars=1800)` | Готовый компактный контекстный блок из топ-чанков — для промптов локальных LLM. |
| `start_watchkeeper(interval=300)` | Запустить авто-ingestion: сканирует `raw/` каждые N секунд (по умолчанию 300). |
| `stop_watchkeeper()` | Остановить авто-ingestion. |
| `watch_status()` | Статус watchkeeper: сколько сканов, файлов, ошибок. |
| `ocr_scan_images(directory, engine?, languages?)` | Сканировать директорию изображений и извлечь текст через OCR. |
| `ocr_extract_image(image_path, engine?, languages?)` | Извлечь текст из одного изображения через OCR. |
| `ocr_status()` | Доступные OCR-движки и поддерживаемые форматы. |
| `config_get()` | Показать текущие настройки Nexus. |
| `config_set(section, key, value)` | Обновить одно значение в конфиге. |
| `config_update(settings)` | Обновить несколько значений за раз. |
| `web_fetch(url, extract_content?, timeout?)` | Скачать веб-страницу и вернуть контент. |
| `web_save(url, project_id?, timeout?)` | Скачать и сохранить веб-страницу в `raw/` для индексации. |
| `web_status()` | Доступные способы скачивания (requests / urllib). |
| `orch_start_task(task_id, agent_id, project_id, description?, model?, variant?)` | Запустить задачу в оркестраторе. |
| `orch_end_task(task_id, agent_id, session_data?)` | Завершить задачу и сохранить сессию. |
| `orch_status()` | Статус оркестратора: задачи, агенты, проекты. |
| `orch_list_tasks(agent_id?, project_id?, status?)` | Список задач с фильтрами. |

---

## 🖥️ CLI-утилита `nexus`

Для работы без MCP — командная строка:

```bash
cd e:\VSCodeProjects\Nexus

# Добавить знание
python nexus_cli.py add "blake3 быстрее SHA-256 в 3 раза" --tags "#hashing" --project "MyProject"

# Поиск (ключевой)
python nexus_cli.py search "хэширование"

# Семантический поиск
python nexus_cli.py search "векторный поиск" --semantic

# Контекст для агента
python nexus_cli.py context --agent cline

# Контекст-промт для локальных LLM
python nexus_cli.py context-prompt --query "как работать с Flet" --agent cline

# Ингестия документов
python nexus_cli.py ingest --max 10

# Автосводка сессии
python nexus_cli.py summary --agent cline

# Совместные решения
python nexus_cli.py decisions --project "MyProject"

# Авто-ingestion (watchkeeper)
python nexus_cli.py watch --interval 300     # запустить (блокирует)
python nexus_cli.py watch --status            # показать статус
python nexus_cli.py watch --toggle            # старт/стоп
```

---

## 📥 Ingestion Pipeline (обработка сырых документов)

Nexus умеет автоматически превращать сырые файлы в структурированные чанки библиотеки.

**Пути:**
- Вход (сюда класть/скачивать документы): `nexus_store/input_docs/raw/`
- Архив обработанных: `nexus_store/input_docs/processed/`
- Лог: `nexus_store/_logs/ingestion.jsonl`

**Поддерживаемые форматы:** `.md`, `.markdown`, `.txt`, `.text`, `.json`, `.jsonl`, `.csv`, `.html`, `.htm`, `.pdf`
(остальное — `.png`, `.jpg`, `.jpeg`, `.bmp`, `.tiff`, `.webp` — обрабатываются через OCR, если установлен pytesseract или easyocr;
`.gif` и т.д. — помечается как `skipped` в логе и остаётся в `raw/`).

**Что делает пайплайн для каждого файла:**
1. Извлекает текст (JSON → pretty-print, CSV → Markdown-таблица, HTML → текст с заголовками `#`, PDF → pypdf/PyMuPDF, PNG/JPG/BMP/TIFF → OCR: pytesseract или easyocr).
2. Читает front-matter из `.md` (`tags: [...]`, `project: <id>`, `source: <url>`) — они становятся тегами/привязкой чанка.
3. Разбивает на семантические чанки ~1200 символов (по заголовкам в Markdown).
4. Индексирует каждый чанк через `add_chunk` (теги `#doc`, `#source:<kind>` + теги из front-matter).
5. Перемещает исходник в `processed/`.

**Запуск из командной строки:**
```bash
cd e:\VSCodeProjects\Nexus
python -m core.ingestion
```

**Запуск через MCP (из сессии агента):**
```
run_ingestion(max_files=10)   // вернёт отчёт: сколько файлов обработано
get_ingestion_status()        // покажет последние записи лога
```

---

## 🔄 Watchkeeper (авто-ingestion)

Watchkeeper автоматически сканирует `input_docs/raw/` каждые N секунд и запускает
IngestionPipeline для новых файлов. Больше не нужно вручную запускать `run_ingestion`.

**Запуск из командной строки:**
```bash
cd e:\VSCodeProjects\Nexus

# Запустить в фоне (блокирует терминал, Ctrl+C для остановки)
python nexus_cli.py watch --interval 300

# Показать статус
python nexus_cli.py watch --status

# Переключить (старт/стоп)
python nexus_cli.py watch --toggle
```

**Запуск через MCP (из сессии агента):**
```
start_watchkeeper(interval=300)   // запустить, интервал 300 сек
stop_watchkeeper()                 // остановить
watch_status()                     // показать статус
```

**Лог:** `nexus_store/_logs/watchkeeper.jsonl`

---

## 📋 Сценарий работы агента

### 1. Начало сессии — загрузить контекст
Вызвать `get_context` сразу после старта:

```
get_context(
  agent_id="cline",
  project_id="MyProject"   // опционально
)
```
Ответ: сводка последней сессии этого агента + общие знания проекта.

### 2. Во время работы — сохранять знания
Когда агент узнал/сделал что-то ценное:

```
add_note(
  content="blake3 выбран для хэширования: в 3 раза быстрее SHA-256 и не требует OpenSSL.",
  tags=["#architecture", "#hashing", "#decision"],
  project_id="MyProject",
  agent_id="cline",
  source="https://example.com/efficient-hashing"
)
```

Когда нужно вспомнить:

```
search_knowledge(
  query="хэширование",
  tags=["#hashing"],
  project_id="MyProject"
)
```

### 3. Совместная работа — фиксировать решения
Если решение принято несколькими агентами (например, Cline + Gigachat):

```
record_joint_decision(
  project_id="MyProject",
  decision="Используем blake3 для всех хэшей.",
  by_agents=["cline", "gigachat"],
  reason="Быстрее, безопаснее, без внешних зависимостей."
)
```

### 4. Конец сессии — сохранить всё одним вызовом
```
end_session(
  agent_id="cline",
  session_data={"chat_history": [...], "actions": [...], "decisions": [...], "next_steps": [...]},
  // collapse_after=true — заодно свернуть старые архивы (keep_last=1 по умолчанию)
)
// вернёт пути к архиву и автосводке + статистику (сделано/решений/дальше)
```

Ручная альтернатива (если нужно больше контроля) — по-прежнему доступна:
```
archive_current_session(agent_id="cline", session_data={...})
generate_session_summary(agent_id="cline")
```

**Сводка — то, что агент «вспомнит» в следующей сессии.** Структурируйте
`session_data` понятными ключами (`actions`, `decisions`, `next_steps`,
`chat_history`, можно по-русски: `сделано`, `решения`, `дальше`) — тогда
автосводка получится точной. Ручная сводка через `store_session_summary`
тоже доступна (например, если хочется добавить контекст, которого нет в архиве).

---

## 🔧 Для разработчиков

### Модули

| Файл | Назначение |
| :--- | :--- |
| `core/nexus_core.py` | Ядро: класс `Nexus` (add_chunk, search, archive_session, get_context, add_joint_decision). |
| `core/ingestion.py` | Ingestion Pipeline: `raw/` → нормализация → чанки → индекс → `processed/`. |
| `core/ocr.py` | OCR-экстрактор: pytesseract + easyocr fallback для изображений (Этап 6). |
| `core/summarizer.py` | SessionSummarizer: автосводки из архивов сессий + сворачивание истории (Этап 3). |
| `core/session_hook.py` | SessionHook: «конец сессии → save» одним вызовом (архив + автосводка + collapse) (Этап 3). |
| `core/semantic.py` | SemanticSearch: векторный поиск (HashingEmbedder, zero-dep), промпт-блоки, кэш (Этап 4). |
| `core/watchkeeper.py` | Watchkeeper: авто-ingestion, сканирует `raw/` каждые N сек (Этап 7). |
| `core/config.py` | Config: загрузка `nexus_config.json`, глобальные настройки (Этап 7). |
| `core/web.py` | Web: скачивание страниц по URL, content extraction (Этап 6). |
| `core/orchestrator.py` | Orchestrator: multi-agent task management (Этап 7). |
| `nexus_mcp_server.py` | MCP-сервер (SDK `mcp>=2.1`, класс `MCPServer`). |
| `nexus_mcp_config.json` | Готовый конфиг MCP для клиентов. |
| `nexus_config.json` | Файл конфигурации (пути, лимиты, OCR, watchkeeper). |
| `nexus_store/` | Все данные системы (файлы + JSON-индексы). |
| `AGENT_INSTRUCTIONS.md` | Инструкция для агентов: как подключиться и работать с Nexus. |
| `ROADMAP.md` | Мастер-план развития продукта (этапы, статусы, бэклог). |

### Быстрая проверка ядра из Python

```bash
cd e:\VSCodeProjects\Nexus
python -c "from core.nexus_core import Nexus; nm = Nexus(); print('ok')"
```

### pytest-набор (140 тестов)

```bash
cd e:\VSCodeProjects\Nexus
pytest tests/ -v                    # все тесты
pytest tests/test_nexus_core.py -v  # только ядро
pytest tests/test_summarizer.py -v  # только сводки
pytest tests/test_semantic.py -v    # только семантика
pytest tests/test_ingestion.py -v   # только ingestion (включая PDF)
pytest tests/test_nexus_cli.py -v   # только CLI
```

| Модуль | Тестов | Время |
| :--- | :---: | :---: |
| `test_nexus_core.py` | 27 | ~0.3s |
| `test_summarizer.py` | 26 | ~0.3s |
| `test_session_hook.py` | 8 | ~0.1s |
| `test_semantic.py` | 18 | ~0.3s |
| `test_ingestion.py` | 44 | ~0.4s |
| `test_nexus_cli.py` | 11 | ~0.1s |
| `test_watchkeeper.py` | 20 | ~0.2s |
| `test_ocr.py` | 19 | ~0.3s |
| `test_config.py` | 24 | ~0.3s |
| `test_web.py` | 27 | ~0.3s |
| `test_orchestrator.py` | 25 | ~0.3s |

### Разграничение памяти по агентам

- **Личное** (agent_id): заметки агента, его сводки сессий, его архивы.
- **Общее по проекту** (project_id): все участники проекта видят и пишут.
- **Совместное** (`record_joint_decision`): решения, помеченные списком агентов.

Агент `cline` не увидит личные заметки агента `gigachat`, но оба увидят общие
знания проекта и совместные решения.

---

## 🗺️ Дорожная карта (что дальше)

> Полный мастер-план — в [ROADMAP.md](./ROADMAP.md).

- [x] **Ingestion pipeline** — автообработка `input_docs/raw/` → единый MD → чанки → индекс
- [x] **Автосводки сессий** — эвристическая автогенерация из архива + сворачивание истории (SessionSummarizer, без LLM)
- [x] **Семантический поиск** — векторные эмбеддинги (HashingEmbedder, zero-dep; sentence-transformers — опционально)
- [x] **Промпты-инъекции** — готовые блоки контекста `build_context_prompt` с бюджетом символов
- [x] **PDF Ingestion** — поддержка `.pdf` через pypdf + PyMuPDF fallback
- [x] **CLI-утилита `nexus`** — `add`, `search`, `ingest`, `context`, `summary`, `decisions`, `context-prompt`
- [x] **Watchkeeper** — авто-ingestion: сканирует `raw/` каждые N сек, запускает пайплайн
- [x] **OCR изображений** — поддержка `.png/.jpg/.jpeg/.bmp/.tiff/.webp` через pytesseract + easyocr fallback
- [x] **pip-упаковка** — `pip install -e ".[ocr]"`, `nexus` CLI из командной строки, optional deps
- [x] **Конфиг пользователя** — `nexus_config.json`: пути, лимиты, OCR, auto-start; MCP + CLI
- [x] **Веб-страницы по URL** — скачивание HTML → content extraction → `raw/` → пайплайн
- [x] **Оркестратор** — multi-agent task management: start/end/cancel, agent tracking, project stats
- [ ] **EPUB / DOCX** — дополнительные форматы документации

---

## 💬 Итог

Nexus — это рабочая система памяти, которую можно использовать уже сейчас:
добавлять знания, искать, запоминать контекст сессий и фиксировать совместные
решения. Она развивается итеративно — как и любой инструмент «сопроцессора».