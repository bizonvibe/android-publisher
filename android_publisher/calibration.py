"""Local, interactive selector capture. It never clicks the selected element."""

import itertools
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from .android import PACKAGE, Android, canonical_permalink
from .models import Stop
from .selectors import resolve

LABELS = {
    "profile_tab": "вкладка вашего профиля",
    "account_username": "username активного аккаунта (не отображаемое имя)",
    "reel": "выбор Reels в меню импорта (не финальная кнопка Share)",
    "editor_next": "Next/Далее из редактора к описанию",
    "caption": "поле описания",
    "trial_toggle": "переключатель Пробные/Trial; absent только если функции действительно нет",
    "automatic_share_toggle": "переключатель АВТОМАТИЧЕСКИ поделиться со всеми: должен быть выключен",
    "trial_mode_marker": "подтверждение Trial: активный переключатель или однозначная подпись",
    "normal_mode_marker": "подтверждение обычного режима: выключенный Trial либо однозначная подпись",
    "composer_account": "username на финальном экране; owner если Instagram здесь его не показывает",
    "publish_next": "Далее на форме с описанием: может начать отправку; сохраняется без нажатия",
    "publication_confirmation": "заголовок окна подтверждения (например Обновление оригинального аудио)",
    "publish": "ФИНАЛЬНАЯ кнопка Поделиться/Share; выберите, но НЕ нажимайте",
    "publication_success": "явное сообщение об успешной публикации этого ролика, не вкладка ленты",
}


def literal(value):
    if "'" not in value:
        return "'" + value + "'"
    if '"' not in value:
        return '"' + value + '"'
    return "concat(" + ',"\'",'.join("'" + part + "'" for part in value.split("'")) + ")"


def candidates(xml):
    root = ET.fromstring(xml)
    nodes = [node for node in root.iter("node") if node.get("password") != "true"]
    parents = {child: parent for parent in root.iter() for child in parent}
    mapping = {
        "resource-id": "resourceId",
        "content-desc": "description",
        "text": "text",
        "class": "className",
    }
    result = []
    for node in nodes:
        attrs = [(key, node.get(key)) for key in mapping if node.get(key)]
        if not attrs:
            continue
        spec = None
        for size in range(1, len(attrs) + 1):
            for combination in itertools.combinations(attrs, size):
                if sum(all(other.get(k) == v for k, v in combination) for other in nodes) == 1:
                    spec = {mapping[k]: v for k, v in combination}
                    break
            if spec:
                break
        # A labelled ancestor is often needed for repeated Android Switch widgets.
        if spec is None:
            ancestor = parents.get(node)
            while ancestor is not None:
                for key in ("resource-id", "content-desc", "text"):
                    value = ancestor.get(key)
                    descendants = [
                        child
                        for child in ancestor.iter("node")
                        if child.get("class") == node.get("class")
                        and child.get("checkable") == node.get("checkable")
                    ]
                    if (
                        value
                        and sum(other.get(key) == value for other in nodes) == 1
                        and len(descendants) == 1
                    ):
                        spec = {
                            "xpath": f"//*[@{key}={literal(value)}]//*[name()={literal(node.get('class', ''))} and @checkable={literal(node.get('checkable', 'false'))}]"
                        }
                        break
                if spec:
                    break
                ancestor = parents.get(ancestor)
        if spec:
            result.append(
                {
                    "selector": spec,
                    "text": node.get("text", ""),
                    "description": node.get("content-desc", ""),
                    "class": node.get("class", ""),
                    "bounds": node.get("bounds", ""),
                    "checked": node.get("checked") == "true",
                    "checkable": node.get("checkable") == "true",
                }
            )
    return result


def save_profile(path, profile):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class Calibrator:
    def __init__(self, android, ask=input, output=print):
        self.android, self.ask, self.output = android, ask, output

    def capture(self, name, optional=False):
        a = self.android
        while True:
            a.guard()  # Never continue calibration through a challenge/login screen.
            a.checkpoint("calibrate-" + name)
            self.output("\nНастройка: " + name + " — " + LABELS.get(name, name))
            self.output(
                "Смотрите телефон и screen.png в каталоге шагов. Финальную Share НЕ нажимайте."
            )
            choices = candidates(a.d.dump_hierarchy())
            for index, item in enumerate(choices, 1):
                label = (item["text"] or item["description"] or item["class"])[:100]
                self.output(f"{index}. {label} checked={item['checked']} {item['bounds']}")
            extra = "; absent=функции нет" if optional else ""
            if name in {"reel", "editor_next", "gallery_next", "create", "gallery"}:
                extra += "; skip=этот промежуточный экран отсутствует"
            if name == "composer_account":
                extra += "; owner=ручная проверка владельцем"
            response = self.ask(
                "Номер элемента; r=обновить после ручного перехода; q=стоп" + extra + ": "
            ).strip()
            if response == "q":
                raise Stop("OWNER_CANCELLED")
            if response == "r":
                continue
            if response == "skip" and name in {
                "reel",
                "editor_next",
                "gallery_next",
                "create",
                "gallery",
            }:
                a.profile.setdefault("skipped_steps", []).append(name)
                self.persist()
                return None
            if response == "absent" and optional:
                a.profile.setdefault("unavailable", []).append(name)
                a.profile.setdefault("selectors", {})[name] = []
                self.persist()
                return None
            if response == "owner" and name == "composer_account":
                a.profile["composer_account_verification"] = "owner"
                self.persist()
                return None
            try:
                item = choices[int(response) - 1] if int(response) > 0 else None
            except (ValueError, IndexError):
                item = None
            if item is None:
                self.output("Выберите существующий номер.")
                continue
            spec = dict(item["selector"])
            # Markers selected from a switch must bind the observed state, not merely its existence.
            if name in {"trial_mode_marker", "normal_mode_marker"} and item["checkable"]:
                if "xpath" in spec:
                    spec["xpath"] += "[@checked=" + literal(str(item["checked"]).lower()) + "]"
                else:
                    spec["checked"] = item["checked"]
            selected = resolve(a.d, spec)
            if not selected.exists or selected.count != 1:
                self.output("Элемент не уникален/экран изменился. Обновите список.")
                continue
            if self.ask("Сохранить этот селектор без нажатия? Введите SAVE: ").strip() != "SAVE":
                continue
            a.profile.setdefault("selectors", {})[name] = [spec]
            if name in a.profile.get("unavailable", []):
                a.profile["unavailable"].remove(name)
            self.persist()
            return selected

    def persist(self):
        a = self.android
        a.profile["live_calibrated"] = False
        a.profile["calibration"] = {
            "device_serial": a.settings.device_serial,
            "app_version": a.d.app_info(PACKAGE).get("versionName"),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        save_profile(a.settings.selector_profile, a.profile)


class GuidedAndroid(Android):
    def __init__(self, settings, *, calibrate=True, ask=input, output=print):
        super().__init__(settings)
        self.ask, self.output, self.calibrate_missing = ask, output, calibrate
        self.calibrator = Calibrator(self, ask, output)
        self.owner_session = False

    def selector(self, name, required=True):
        if name in self.profile.get("skipped_steps", []) and name in {
            "reel",
            "editor_next",
            "gallery_next",
            "create",
            "gallery",
        }:
            return None
        obj = super().selector(name, required=False)
        if obj is not None:
            return obj
        if (
            name == "composer_account"
            and self.profile.get("composer_account_verification") == "owner"
        ):
            return None
        optional = name == "trial_toggle"
        if not required and not optional:
            return None
        if optional and name in self.profile.get("unavailable", []):
            return None
        if self.calibrate_missing:
            return self.calibrator.capture(name, optional=optional)
        if required:
            self.output(
                "Откройте на телефоне экран для шага " + name + ". Финальную кнопку не нажимайте."
            )
            if self.ask("Enter=проверить сохранённый селектор; q=стоп: ").strip() == "q":
                raise Stop("OWNER_CANCELLED")
            return super().selector(name, required=True)
        return None

    def wait(self, name, seconds=15):
        if name == "publication_success":
            return super().wait(name, seconds)
        # Interactive capture occurs immediately, not once per half-second poll.
        obj = self.selector(name, required=True)
        if obj is None:
            raise Stop("UI_NOT_FOUND:" + name)
        return obj

    def confirm_composer_account(self, expected):
        if self.profile.get("composer_account_verification") == "profile_before_import":
            return super().confirm_composer_account(expected)
        if self.profile.get("composer_account_verification") != "owner":
            obj = self.selector("composer_account")
            if obj is not None:
                if obj.get_text().strip().lstrip("@").casefold() != expected.casefold():
                    raise Stop("ACCOUNT_MISMATCH")
                return
        self.output(
            "Instagram не показывает username на этом экране. Проверьте аккаунт и подготовленный ролик на телефоне."
        )
        if (
            self.ask("Для подтверждения владельцем введите @" + expected + ": ").strip()
            != "@" + expected
        ):
            raise Stop("ACCOUNT_MISMATCH")
        self.checkpoint("account-confirmed-by-owner")

    def submit(self, prepared):
        if self.profile.get("selectors", {}).get("publication_success"):
            result = super().submit(prepared)
            result["verification"] = "ui-success-marker"
            return result
        # Local owner session only: never confuse human verification with an automatic UI assertion.
        if not self.owner_session or prepared["final_action"] == "publish_next":
            raise Stop("SUCCESS_SELECTOR_REQUIRED")
        self.tap_final(prepared)
        self.checkpoint("sent-awaiting-owner-verification")
        self.output(
            "Одно финальное нажатие отправлено. Дождитесь обработки Instagram. НЕ нажимайте Share повторно."
        )
        url = self.ask(
            "Откройте именно опубликованный ролик в своём аккаунте и скопируйте permalink; пусто=исход неизвестен: "
        ).strip()
        url = canonical_permalink(url)
        if not url:
            raise Stop("UNVERIFIED_RESULT")
        if (
            self.ask(
                "Убедитесь, что совпали ролик, аккаунт и режим. Для сохранения результата введите VERIFIED: "
            ).strip()
            != "VERIFIED"
        ):
            raise Stop("UNVERIFIED_RESULT")
        self.guard()
        self.checkpoint("publication-verified-by-owner")
        return {"verified": True, "permalink": url, "verification": "owner-confirmed"}
