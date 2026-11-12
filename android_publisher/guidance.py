"""Safe, operator-facing errors; never print raw SDK exception strings."""

HINTS = {
    "INVALID_CONFIRMATION_SNAPSHOT": "В XML нет однозначного заголовка и обычной кнопки Поделиться ожидаемого окна Instagram. Профиль не изменён, нажатий не было.",
    "CONFIRMATION_SNAPSHOT_NOT_FOUND": "Среди data/jobs не найден XML окна Обновление оригинального аудио. Можно передать сохранённый файл через import-confirmation --xml путь. Ничего не нажимайте ради поиска старого снимка.",
    "DAILY_NOT_ENABLED": "Ежедневный LIVE ещё не разрешён. Нужны откалиброванный профиль, успешный LIVE тест и enable-daily --pilot-job UUID.",
    "DAILY_PROFILE_INCOMPLETE": "Для ежедневной отправки откалибруйте publish_next, publication_confirmation, publish и publication_success. Затем approve-profile. Не сохраняйте Далее вместо финального Share.",
    "VERIFIED_PILOT_REQUIRED": "Нужен PUBLISHED тест NORMAL_REEL в crabscoder, проверенный UI-маркером успеха с тем же телефоном/версией/профилем. Ручное подтверждение не включает ежедневный автомат.",
    "KEYBOARD_STATE_UNKNOWN": "Android не сообщил однозначное состояние клавиатуры. Автоматическое Назад не нажато. Сохраните вывод adb shell dumpsys input_method для адаптации диагностики; не подставляйте случайный селектор.",
    "KEYBOARD_NOT_HIDDEN": "После единственного автоматического Назад клавиатура не скрылась. Повторное Назад не отправляется, чтобы не выйти из подготовки. Проверьте screenshot/stop.json.",
    "DEVICE_CHECKSUM_MISMATCH": "Переданный MP4 на телефоне не совпал по SHA256. Проверьте свободное место и USB, удалите повреждённый файл из Movies/Publisher, повторите DRY_RUN. Instagram ещё не получил этот файл.",
    "R2_ENDPOINT_INVALID": "Укажите HTTPS endpoint Cloudflare R2: https://ACCOUNT_ID.r2.cloudflarestorage.com (account ID — 32 hex символа). Можно задать R2_ACCOUNT_ID.",
    "FINAL_CONTROL_AT_PREPARATION": "Подготовительный шаг совпал с Share/Опубликовать. Нажатие заблокировано. Повторите калибровку промежуточной кнопки; если экран отсутствует, выберите skip. Финальная кнопка — только publish.",
    "OWNER_CONFIRMATION_REQUIRED": "Это локальное LIVE-задание требует присутствия владельца. API/рестарт не имеют права его выполнить. Сделайте новый DRY_RUN и подтвердите локально.",
    "DRY_RUN_REQUIRED": "Сначала выполните dry-run --interactive до DRY_RUN_READY. Его UUID нужен для будущего LIVE.",
    "DRY_RUN_EXPIRED": "С момента DRY_RUN прошло более 30 минут. Подготовьте ролик заново в DRY_RUN.",
    "CAPTION_MISMATCH": "Instagram не подтвердил полный текст описания. Проверьте поле и разрешения клавиатуры UIAutomator2; повторите DRY_RUN, не нажимая Share.",
    "TEMPORARY_FAILURE": "Временная ошибка связи до финального действия. Проверьте USB/интернет и doctor; число автоматических повторов ограничено.",
    "UNEXPECTED_APPLICATION": "На экране другое приложение/системный диалог. Разрешите доступ к видео или выберите Instagram вручную, затем повторите DRY_RUN. Защитные запросы не обходите.",
    "ADB_UNAVAILABLE": "Проверьте путь к adb.exe, USB-кабель и adb devices -l. Подтвердите RSA на телефоне.",
    "DEVICE_UNAVAILABLE": "Разблокируйте Android, включите USB debugging; ожидается SERIAL device, не unauthorized/offline.",
    "ACCOUNT_MISMATCH": "Вручную переключите Instagram на username задания. Повторите DRY_RUN.",
    "FEATURE_UNAVAILABLE": "Штатная функция Instagram не найдена. Не заменяйте Trial обычной публикацией. Проверьте доступность вручную.",
    "HUMAN_INTERVENTION_REQUIRED": "Остановлено: вход, капча, проверка личности или ограничение. Решите вопрос вручную в Instagram. Не обходите проверку. После проверки снимите паузу командой resume.",
    "UNKNOWN_RESULT": "Не нажимайте Share ещё раз. Проверьте профиль/пробные ролики на телефоне. Даже после resume это задание повторно не отправляется.",
    "EXACT_MEDIA_NOT_FOUND": "Нельзя однозначно выбрать файл. Используйте мастер с импортом share_intent, либо откалибруйте точный filename-селектор.",
    "MEDIASTORE_NOT_FOUND": "Файл передан, но MediaStore не нашёл его. Разрешите Instagram доступ к видео, дождитесь индексации и повторите DRY_RUN.",
    "R2_NOT_CONFIGURED": "Задайте R2_ENDPOINT или R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY в переменных окружения. Секреты в файлы не сохраняются.",
    "R2_OBJECT_UNAVAILABLE": "Проверьте точные ключи MP4/manifest и право GetObject для content-factory-videos. Не нужен публичный bucket.",
    "VIDEO_CHECKSUM_MISMATCH": "MP4 не совпадает с manifest. Не публикуйте: проверьте исходный объект и SHA256 в R2.",
    "VIDEO_MIME_MISMATCH": "У MP4 ожидается MIME video/mp4 и в manifest, и в R2. Исправьте Content-Type исходного объекта.",
    "MANIFEST_MISMATCH": "Manifest ссылается на другой MP4. Проверьте r2_object_key и r2_manifest_key, не подставляйте другой ролик.",
    "MANIFEST_NOT_READY_OR_MISMATCH": "Ожидается ready manifest фабрики v1 с тем же prefix, что у MP4.",
    "DUPLICATE_PUBLICATION": "Этот ролик/его SHA256 уже отправлялся либо имеет неизвестный исход. Проверьте прежнее задание; смена ключа не является разрешением на повтор.",
    "LIVE_DISABLED": "LIVE выключен. Сейчас выполните DRY_RUN. Для будущего LIVE требуется отдельное разрешение в окружении и подтверждение владельца перед кнопкой.",
    "OWNER_CANCELLED": "Публикация отменена владельцем до финального действия. Ничего не отправлено.",
    "DRY_RUN_CHANGED": "Профиль, приложение, телефон или байты видео изменились после DRY_RUN. Выполните новый DRY_RUN.",
    "DEVICE_PAUSED": "Устройство на паузе после ограничения или неизвестного исхода. Проверьте телефон вручную, затем resume. Старые задания не повторяются.",
}


def guidance(code):
    if code.startswith(
        ("UI_NOT_FOUND:", "AMBIGUOUS_UI:", "UNVERIFIABLE_TOGGLE:", "TOGGLE_FAILED:")
    ):
        step = code.split(":", 1)[1]
        return f"Шаг {step}: откройте сохранённый screen.png/XML. Выполните calibrate --step {step} либо dry-run --interactive; выберите уникальный элемент. Финальную кнопку вручную не нажимайте."
    return HINTS.get(
        code,
        "Изучите stop.json и скриншоты шагов. Запустите doctor --job data/first-job.json --download. Не повторяйте отправку при submitted=1.",
    )
