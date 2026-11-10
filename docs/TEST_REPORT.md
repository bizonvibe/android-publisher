# Проверки пути к первому DRY_RUN — 2026-11-10

Локально, Linux/Python 3.12:

- `python -m pytest -q`: **94 passed**. Один upstream Starlette/httpx deprecation warning, не ошибка.
- `python -m ruff check .`: passed.
- `git diff --check`: passed.
- `python -m android_publisher --help`: passed. Проверка подпроцесса с `PYTHONIOENCODING=cp1252` защищает русский вывод CLI на Windows от UnicodeEncodeError.
- Read-only `diagnose()` в текущем облаке корректно вернул `ADB_UNAVAILABLE` и `R2_NOT_CONFIGURED`, не сообщил ложный успех.

CI проверяет Ubuntu/Windows с Python3.11/3.12: те же тесты, линтер, CLI smoke; на Windows дополнительно разбирает PowerShell launcher без исполнения настройки/публикации. Результаты запусков смотрите в PR/Actions.

Содержательные проверки: factory R2-v1 контракт по фактически прочитанному exporter, точные данные сохранённой remote verification, legacy fixtures, неправильный prefix/status/key/MIME/hash/размер, проверенное описание, полный путь R2 adapter → SQLite → Mock Android → DRY_RUN_READY, оба режима DRY_RUN, настройки без сохранения секретов/LIVE, калибровка без кликов, XPath на реальном offline PageSource UIAutomator2, точный MediaStore URI, SHA256 телефона, отказ нажать Share как промежуточную кнопку, подтверждение владельца/отказ/изменённые байты, запрет исполнения локального LIVE после рестарта без владельца, дедупликация по SHA256 даже с другим video_id, сохранение старой идемпотентности после расширения схемы, ошибки/скриншоты/инструкции.

**Не выполнено:** физический Android, USB-драйверы конкретного Windows, реальные экраны Instagram, повторное чтение приватного R2 из этого облака и настоящая публикация. Windows/телефон не подключены к окружению по сообщению владельца; R2 credentials здесь не заданы. LIVE не включался. Тестовый manifest fixture — реконструкция по коду exporter и сохранённому upstream-отчёту, не прямое скачивание приватного объекта; provenance описан в MANIFEST_COMPATIBILITY.md.

Следующий аппаратный шаг владельца: установить Platform Tools, подключить/авторизовать телефон, войти в Instagram, запустить `scripts/Start-Publisher.ps1`, пройти doctor и интерактивный DRY_RUN. Ожидается DRY_RUN_READY перед финальным Share. При остановке сохранить stop.json и screen.png/steps; кнопку Share не нажимать. Любые капчи/проверки решать вручную, без обхода.
