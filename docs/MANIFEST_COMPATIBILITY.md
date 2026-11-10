# Проверка реального manifest генератора

Актуальная R2-фабрика находится **в ветке `feature/ru-reels-r2`**, не в `main` репозитория `bizonvibe/insta-reels-agent`. Проверен commit `05ce0afc2a6f537f7f379775e68d239a7182d9f4`:

- [factory/r2.py](https://github.com/bizonvibe/insta-reels-agent/blob/05ce0afc2a6f537f7f379775e68d239a7182d9f4/factory/r2.py), `upload_ready` создаёт manifest, `verify_uploaded` проверяет реальные байты.
- [Контракт и пути](https://github.com/bizonvibe/insta-reels-agent/blob/05ce0afc2a6f537f7f379775e68d239a7182d9f4/docs/RUSSIAN_REELS_R2.md).
- [Сохранённая независимая проверка вашего ролика](https://github.com/bizonvibe/insta-reels-agent/blob/05ce0afc2a6f537f7f379775e68d239a7182d9f4/docs/reels/2026-11-09-bizon-vibe-agent-check/r2-verification.json).

Формат, непосредственно прочитанный в коде экспортёра:

```json
{
  "version": 1,
  "status": "ready",
  "provider": "cloudflare-r2",
  "language": "ru",
  "channel": "Bizon Vibe",
  "prefix": "reels/ru/reel-20261109-92645916",
  "assets": {
    "video": {
      "key": "reels/ru/reel-20261109-92645916/video.mp4",
      "sha256": "5de5a375c395b646fd372e7dfaf85afc71805eddffb390b11cb1807f687659e0",
      "bytes": 5419698,
      "content_type": "video/mp4"
    }
  }
}
```

Это сокращённый пример: фактический экспортёр также сохраняет `cover`, `subtitles`, `description`, `qa`, `scenario` и верхний `qa`. Не переписывайте ваш manifest по примеру. Предыдущий publisher ошибочно ожидал плоские `video_id/object_key/size_bytes` и не был совместим с фабрикой. Теперь основная схема — `factory-r2-v1`, данные берутся из `assets.video`.

Publisher проверяет `version=1`, `status=ready`, provider, точное совпадение prefix/полного ключа MP4, MIME в manifest **и ответе R2**, размер и потоковый SHA256. Для использования описания дополнительно проверяются ключ, MIME, размер и SHA256 `assets.description`; текст показывается владельцу. После загрузки остаётся `download.json` с источником и хешем. Bucket приватный `content-factory-videos`; используется S3 path addressing/SigV4 и настройки checksum, совместимые с `factory/r2.py`. Поддержаны те же ENV `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_ENDPOINT`/`R2_ACCOUNT_ID`; `PUBLISHER_*` имеют приоритет.

Указанный владельцем исходный объект и версия `director-v2` — разные экспорты. Мастер использует **ровно исходные ключи владельца**, не подменяет их `director-v2`. При несовпадении хеша/байтов публикация блокируется.

Файл `tests/fixtures/agent/r2-verification.json` скопирован из сохранённого upstream-отчёта. `factory-r2-manifest.json` — тестовая реконструкция контракта по коду экспортёра и этому отчёту, **не прямое скачивание приватного manifest**. Повторное обращение к R2 из облака не выполнено: credentials здесь отсутствуют. `doctor --job data/first-job.json --download` выполняет настоящую проверку на Windows. Это не заменяется утверждением о предыдущей загрузке.

Для обратной совместимости дополнительно проверены fixtures из `main@939e8f29442e314864c220af1e3c600e45f81636`:

| Формат | Поддержка |
| --- | --- |
| `docs/reels/oct13-four/final-manifest.json`: массив `{file,size_bytes,sha256,...}` | Ровно одна запись с точным basename MP4; явный `r2_manifest_key` |
| `docs/reels/fbo-fbs-misha2/qa.json`: `{file,bytes,sha256,...}` | Нормализация `bytes` |
| `storage-manifest.json`: delivery/Library IDs | Не подходит: нет SHA256/размера |
| Manifest скриншотов из `capture-style03.mjs` | Не подходит для MP4 |
| QA без имени файла либо без размера | Не сопоставляется по догадке |
| Плоский формат publisher-v1 | Поддержан с совпадением video_id/полного object_key |

`video_id` — идентификатор оркестратора, в мастере берётся из prefix. Не путать с HeyGen avatar ID. `r2_manifest_key` задаётся явно; при отсутствии для обратной совместимости читается `manifest.json` рядом с MP4.

Другой репозиторий не изменялся; в нём не запускались генерация, загрузка, TTS, Actions или платные API. R2 publisher только читает; не создаёт бакеты/объекты и не требует публичного доступа.
