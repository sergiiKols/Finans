"""ПЕРЕНОС — баланс на следующий месяц из результата расчёта.

Зачем. Баланс переносился руками, с округлённых до копейки значений и без
всякой сверки. Из-за этого 275.26 руб. фонда управления не доехали из августа
в сентябрь: строки в файле просто не оказалось, и никто этого не заметил.
Карманы не переносились вовсе — поля для них в балансе не было.

Этот скрипт собирает баланс сам, ничего не теряя:
  * имущественные паи — в полной точности, без округления;
  * ВСЕ фонды, включая нулевые и те, что остались невыплаченными;
  * остатки карманов;
  * долг резервному фонду и счётчик дефицитных месяцев подряд;
  * контрольную сумму, которую ядро проверяет при чтении.

Запуск:  python3 perenos.py <месяц.json> [> balans-sleduyushchiy.json]
"""
import io
import json
import os
import sys
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("raschet", os.path.join(HERE, "raschet.py"))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def sobrat(par, mes):
    """Посчитать месяц и вернуть баланс на следующий."""
    tiho = io.StringIO()
    _stdout = sys.stdout
    sys.stdout = tiho                      # расчёт печатает по дороге — нам нужен результат
    try:
        u = R.schitat(par, mes)
    finally:
        sys.stdout = _stdout

    # Сборка одна на всех — в ядре. Свою копию держать нельзя: в кабинете была
    # вторая, с другим набором полей, и месяцы из файлов переносились иначе,
    # чем месяцы из журнала.
    return R.balans_na_sleduyushchiy(u, par), u


def main():
    mes_file = sys.argv[1] if len(sys.argv) > 1 else "mesyac.json"
    par = json.load(io.open(os.path.join(HERE, "parametry.json"), encoding="utf-8"))
    mes = json.load(io.open(os.path.join(HERE, mes_file), encoding="utf-8"))
    bal, u = sobrat(par, mes)

    # Проверка на месте: перенесённое обязано совпасть с концом месяца.
    poteri = []
    if abs(bal["_svodka"]["pai"] - sum(u.imushch_pai[k] for k in u.uchastniki)) > 1e-9:
        poteri.append("паи")
    if abs(bal["_svodka"]["fondy"] - sum(u.fondy.values())) > 1e-9:
        poteri.append("фонды")
    if abs(bal["_svodka"]["karmany"] - sum(u.karmany.values())) > 1e-9:
        poteri.append("карманы")
    if poteri:
        sys.stderr.write("! перенос теряет: " + ", ".join(poteri) + "\n")
        sys.exit(1)

    sys.stderr.write(
        f"перенесено: паи {bal['_svodka']['pai']:.2f}, фонды {bal['_svodka']['fondy']:.2f} "
        f"({len(bal['fondy_ostatki'])} шт.), карманы {bal['_svodka']['karmany']:.2f}, "
        f"долг {bal['dolg_rezervu']:.2f}, дефицитных месяцев подряд "
        f"{bal['mesyacev_deficita_podryad']}\n")
    print(json.dumps(bal, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
