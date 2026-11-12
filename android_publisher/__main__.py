"""Run without arguments for the API; local commands never start the API worker."""

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load_settings
from .guidance import guidance
from .models import Mode, Stop


def main(argv=None):
    # Windows redirected consoles may default to cp1252, which cannot encode Russian.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Windows → Android Instagram Reels. LIVE по умолчанию выключен."
    )
    parser.add_argument("--config", type=Path, default=Path("publisher.local.json"))
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="API 127.0.0.1:8080")
    sub.add_parser("setup", help="Мастер: USB, R2, профиль, задание, только DRY_RUN")
    doctor = sub.add_parser("doctor", help="Диагностика без публикации и записи в R2")
    doctor.add_argument("--job", type=Path)
    doctor.add_argument("--download", action="store_true")
    calibration = sub.add_parser("calibrate", help="Сохранить селектор текущего экрана без нажатия")
    calibration.add_argument("--step", required=True)
    imported = sub.add_parser(
        "import-confirmation", help="Сохранить селекторы подтверждения из старого XML без нажатий"
    )
    imported.add_argument("--xml", type=Path)
    dry = sub.add_parser("dry-run", help="Подготовить и остановиться перед Share")
    dry.add_argument("--job", type=Path, default=Path("data/first-job.json"))
    dry.add_argument("--mode", choices=("TRIAL_REEL", "NORMAL_REEL"))
    dry.add_argument("--interactive", action="store_true")
    live = sub.add_parser(
        "live", help="Только явный LIVE после свежего DRY_RUN и подтверждения владельца"
    )
    live.add_argument("--from-dry-run", required=True)
    live.add_argument(
        "--learn-success",
        action="store_true",
        help="Один контролируемый LIVE пилот с записью явного сообщения успеха",
    )
    daily = sub.add_parser(
        "daily", help="Один запуск проверки дневных R2-слотов и публикации без вопросов"
    )
    daily.add_argument("--date", type=str)
    daily.add_argument("--lookback", type=int, default=1)
    daily.add_argument("--live", action="store_true")
    sub.add_parser(
        "configure-daily-flow", help="Подготовить двухшаговый профиль; LIVE не включается"
    )
    sub.add_parser(
        "approve-profile", help="Подтвердить откалиброванный профиль перед локальным LIVE тестом"
    )
    enable = sub.add_parser(
        "enable-daily", help="Разрешить ежедневную публикацию после успешного LIVE теста"
    )
    enable.add_argument("--pilot-job", required=True)
    sub.add_parser("resume", help="Снять паузу ПОСЛЕ ручной проверки, не повторяя старые задания")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command in (None, "serve"):
            import uvicorn

            from .api import create_app

            uvicorn.run(
                create_app(load_settings(args.config)),
                host="127.0.0.1",
                port=8080,
                access_log=False,
            )
            return 0
        from .local import (
            calibrate,
            credentials,
            execute_dry,
            execute_live,
            print_report,
            read_job,
            setup,
        )

        if args.command == "setup":
            return 0 if setup(args.config) is not None else 1
        settings = load_settings(args.config)
        if args.command == "import-confirmation":
            from .confirmation_import import import_confirmation

            result = import_confirmation(settings, args.xml)
            print("Сохранены publication_confirmation и publish из: " + result["source"])
            print(
                "publish_next сохранён без изменений. Нажатий и публикации не было. LIVE выключен в профиле."
            )
            if not result["success_selector_configured"]:
                print(
                    "Остался publication_success — проверка успешной загрузки. Ежедневный LIVE пока не включён."
                )
            return 0
        if args.command in {"doctor", "dry-run", "live"}:
            credentials(settings)
        if args.command in {"daily", "configure-daily-flow", "approve-profile", "enable-daily"}:
            from .daily import approve_profile, configure_flow, enable_daily, run_daily

            if args.command == "daily":
                from datetime import date

                report = run_daily(
                    settings,
                    day=date.fromisoformat(args.date) if args.date else None,
                    lookback=args.lookback,
                    live=args.live,
                )
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return (
                    0
                    if all(
                        row["status"] in {"PUBLISHED", "DRY_RUN_READY", "ALREADY_SUBMITTED"}
                        for row in report["jobs"]
                    )
                    else 1
                )
            if args.command == "configure-daily-flow":
                from .locking import device_session

                with device_session(settings):
                    configure_flow(settings)
                print(
                    "Профиль двух шагов подготовлен; откалибруйте publish_next, publication_confirmation, publish, publication_success. LIVE не включён."
                )
            elif args.command == "approve-profile":
                approve_profile(settings)
                print(
                    "Профиль подтверждён; расписание ещё не включено. Нужен успешный локальный LIVE тест."
                )
            else:
                enable_daily(settings, args.pilot_job)
                print(
                    "Разрешение на ежедневные NORMAL_REEL в @crabscoder сохранено. Установите Windows Task Scheduler."
                )
            return 0
        if args.command == "doctor":
            from .doctor import diagnose
            from .locking import device_session

            with device_session(settings):
                report = diagnose(
                    settings, read_job(args.job) if args.job else None, download=args.download
                )
            path = settings.data_dir / "doctor-report.json"
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print_report(report)
            print("Отчёт: " + str(path))
            return 0 if report["ok"] else 1
        if args.command == "calibrate":
            calibrate(settings, args.step)
        elif args.command == "dry-run":
            job = read_job(args.job)
            if args.mode:
                job.publication_mode = Mode(args.mode)
            row = execute_dry(settings, job, interactive=args.interactive)
            return 0 if row["status"] == "DRY_RUN_READY" else 1
        elif args.command == "live":
            row = execute_live(settings, args.from_dry_run, learn_success=args.learn_success)
            return 0 if row["status"] == "PUBLISHED" else 1
        elif args.command == "resume":
            from .locking import device_session
            from .store import Store

            if (
                input(
                    "Вы вручную проверили ограничения/неизвестный результат? Введите RESUME: "
                ).strip()
                != "RESUME"
            ):
                raise Stop("OWNER_CANCELLED")
            with device_session(settings):
                Store(settings.data_dir / "publisher.sqlite3").pause(False)
            print("Пауза снята. Завершённые/UNKNOWN_RESULT задания не повторяются.")
        return 0
    except Exception as exc:
        # No traceback or raw SDK error values: these can contain credentials.
        code = exc.code if isinstance(exc, Stop) else "LOCAL_COMMAND_FAILED"
        print(code + ": " + guidance(code))
        return 1
    except (KeyboardInterrupt, EOFError):
        print(
            "Остановлено владельцем. Если финальное нажатие уже отправлено, проверьте результат вручную; не повторяйте публикацию."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
