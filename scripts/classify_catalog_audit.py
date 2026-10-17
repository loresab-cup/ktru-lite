"""Apply explicit reviewer rules for obvious product mismatches; not an exact-code gold set."""

import argparse
import json
from collections import Counter
from pathlib import Path


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--input", type=Path, required=True)
    args.add_argument("--output", type=Path, required=True)
    args.add_argument("--report", type=Path, required=True)
    options = args.parse_args()
    audit = json.loads(options.input.read_text(encoding="utf-8"))
    if audit["summary"]["job_id"] != "6ff990ea-dee2-41d8-b461-c3bb7ab25101":
        raise ValueError("Reviewer rules belong only to the explicitly reviewed job")
    # Only clear differences in product identity/material are counted as mismatches.
    # Same category is deliberately NOT called a correct exact KTRU assignment.
    wrong_names = {
        7: {
            "Скарификатор лазерный",
            "Лазерный гравер",
            "Бумага копировальная",
            "Картон канцелярский",
        },
        13: {"Шкаф для одежды металлический", "Шкаф деревянный для документов"},
        15: {"Кабель световой оптоволоконный", "Трубка для ларингэктомии"},
        19: {"Сухари панировочные"},
        21: {"Мороженое", "Масло турбинное", "Масло индустриальное"},
        22: {"Мука гречневая"},
        23: {"Скарификатор лазерный", "Пленка радиографическая промышленная"},
        28: {
            "Дерматоскоп, цифровой",
            "Термостат лабораторный",
            "Термометр метеорологический стеклянный",
            "Емкость для хранения термометров",
        },
        29: {
            "Контейнер лабораторный общего назначения, стерильный",
            "Ксилоловый раствор ИВД",
            "Генератор водорода",
            "Калия теллурит раствор ИВД",
            "Электролизер водородный",
            "Водный раствор мочевины (карбамид)",
        },
        30: {"Спиртовка лабораторная", "Этиловый спирт ИВД, реагент"},
        31: {
            "Микрометры",
            "Термометр метеорологический стеклянный",
            "Аудиометр тональный, автоматический",
            "Вольтамперметр",
        },
        33: {"Скобы для степлера"},
        37: {"Стекло листовое", "Бумага папиросная"},
        38: {"Урна для мусора металлическая", "Мел канцелярский"},
    }
    input_group = {g["input"]: n for n, g in enumerate(audit["groups"], 1)}
    for row in audit["rows"]:
        group = input_group[row["input_name"]]
        wrong = row["catalog_name"] in wrong_names.get(group, set())
        row["semantic_review"] = (
            "obvious_product_mismatch" if wrong else "exact_code_unconfirmed"
        )
        row["review_reason"] = (
            f"Вход: {row['input_name']}; в каталоге выбран: {row['catalog_name']}"
            if wrong
            else "Явное расхождение товарной категории по правилам этой проверки не установлено; точный код не подтверждён"
        )
    rows = audit["rows"]
    bad = [r for r in rows if r["semantic_review"] == "obvious_product_mismatch"]
    bad_auto = [r for r in bad if r["app_status"] == "auto"]
    summary = audit["summary"]
    summary.update(
        {
            "obvious_product_mismatches": len(bad),
            "obvious_product_mismatch_percent": 100 * len(bad) / len(rows),
            "obvious_mismatches_among_auto": len(bad_auto),
            "remaining_exact_code_unconfirmed": len(rows) - len(bad),
            "exact_code_accuracy": None,
            "auto_rows_with_unrequested_child_food_specialization": sum(
                r["app_status"] == "auto"
                and "для детского питания" in (r["catalog_name"] or "").lower()
                and "детск" not in r["input_name"].lower()
                for r in rows
            ),
        }
    )
    audit["review_method"] = (
        "Explicit manual source-product to catalog-name mismatch rules, applied to all saved rows; no re-ranking and no LLM calls; no inference of a correct exact code for unflagged rows."
    )
    audit["review_rules"] = [
        {
            "input_group": k,
            "input_name": audit["groups"][k - 1]["input"],
            "incompatible_catalog_names": sorted(v),
        }
        for k, v in wrong_names.items()
    ]
    options.output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = [
        "# Сверка 1000 назначений КТРУ с базой",
        "",
        "Дата: 17 октября 2026. Проверен результат Яндекса по spec_1000.xlsx, побайтово совпадающему с присланным spec_1000(1).xlsx.",
        f"Задание PostgreSQL: {summary['job_id']}. Прочитано {len(rows)} строк. Повторных платных запросов к модели не было.",
        "",
        "## Итог",
        "",
        "| Проверка | Результат |",
        "|---|---:|",
        f"| Назначенные коды существуют в локальном каталоге | {summary['codes_present']}/1000 |",
        f"| Статус кода ACTIVE | {summary['catalog_statuses'].get('ACTIVE', 0)}/1000 |",
        f"| Название в PostgreSQL совпало с названием по коду каталога | {1000 - summary['saved_name_mismatches']}/1000 |",
        f"| Явно другой товар/материал по сравнению со входом | **{len(bad)}** |",
        f"| Из этих явных ошибок приложение приняло автоматически | **{len(bad_auto)} из 58 auto** |",
        f"| Остальные строки без установленного явного расхождения категории | {len(rows) - len(bad)}; точный код не подтверждён |",
        "| Измеренная точность полного кода КТРУ | не установлена |",
        "",
        "**Наличие действующего кода в базе не означает правильность назначения. Ручная проверка нужна, в том числе для строк auto.**",
        "",
        "## Метод",
        "",
        "Назначения считаны непосредственно из PostgreSQL в транзакции SET TRANSACTION READ ONLY. Коды соединены с актуальным локальным каталогом приложения из 81 509 записей. В БД и каталоге ничего не исправлялось.",
        "Вход содержит 40 исходных описаний товаров, по 25 строк каждого с вариациями характеристик, партий и количеств. Проверены все группы и названия выбранных позиций каталога. Для очевидных расхождений товара/материала зафиксирован явный список несовместимых названий. Правила и построчные результаты сохранены в JSON.",
        "227 — нижняя граница ошибок по этой смысловой проверке, а не полный эталон правильных кодов. Неотмеченные 773 строки нельзя объявлять правильными: требуется проверить конкретный подтип, характеристики, единицы и полноту исходных данных. Пропущенные требования или неподтверждённые специализации не включались в число явных ошибок.",
        "Скорость прежнего прогона (139,093 с, 1000 строк LLM) подтверждает обработку, но не правильность назначения КТРУ.",
        "",
        "## Явные расхождения по группам",
        "",
        "| Исходный товар | Что выбрано в каталоге | Строк | Из них auto |",
        "|---|---|---:|---:|",
    ]
    for g in audit["groups"]:
        members = [r for r in bad if r["input_name"] == g["input"]]
        if not members:
            continue
        labels = Counter(r["catalog_name"] for r in members)
        report.append(
            f"| {g['input']} | {'; '.join(f'{name} — {count}' for name, count in labels.items())} | {len(members)} | {sum(r['app_status'] == 'auto' for r in members)} |"
        )
    report.extend(
        [
            "",
            "## Ошибки среди автоматических назначений",
            "",
            "| Строка Excel | Исходный товар | Выбранный код | Название каталога | Уверенность интерфейса |",
            "|---|---|---|---|---:|",
        ]
    )
    for r in bad_auto:
        report.append(
            f"| {r['row']} | {r['input_name']} | {r['assigned_code']} | {r['catalog_name']} | {r['confidence']}% |"
        )
    report.extend(
        [
            "",
            "## Дополнительные случаи для проверки",
            "",
            "- Все 25 строк обычного ультрапастеризованного молока получили код молока для детского питания. Во входе детское назначение не указано. Из них 13 приняты автоматически. Это неподтверждённая специализация: в 227 явных ошибок не включена.",
            "- Для нитриловых перчаток и части медицинских масок выбрана стерильная разновидность при отсутствии указания стерильности во входном наименовании. Нужны данные о требуемом подтипе.",
            "- Шприцы получили код шприца-дозатора, бинты — рулона марлевого тканого. Одного сходства названий для подтверждения конкретного назначения недостаточно.",
            "- Среди тонометров есть офтальмологическая разновидность при отсутствии назначения во входе. Это требует уточнения и не считалось явной ошибкой автоматически.",
            "- У 9 назначений выбран общий шаблон каталога; его пригодность требует отдельной проверки.",
            "",
            "## Что это означает для приложения",
            "",
            "Сопоставление должно проверять товарную категорию до принятия точного кода. Совпадение отдельных слов вроде «лазерный», «листовой» или «для мусора» недостаточно. Высокая уверенность сейчас не исключает ошибку: три назначения скоб вместо степлера попали в auto. Порог автоматического принятия нельзя оценивать только по тому, что код существует в каталоге.",
            "Нужны защита от явно другой категории, запрос недостающих характеристик и независимый размеченный контрольный набор для точного кода. В рамках этой сверки алгоритм и архитектура не менялись.",
            "",
            "## Материалы",
            "",
            "- yandex-catalog-audit.json — исходное соединение PostgreSQL с каталогом.",
            "- yandex-catalog-semantic-audit.json — явные правила и построчные замечания для всех 1000 строк.",
            "- scripts/audit_catalog_assignments.py и scripts/classify_catalog_audit.py — воспроизведение сверки.",
        ]
    )
    options.report.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
