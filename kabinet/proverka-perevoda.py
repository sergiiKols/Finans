#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ПРОВЕРКА ПЕРЕВОДА — инварианты отметки «перевёл». Живую базу не трогает.

Стенд `yadro/proverka.py` проверяет расчётное ядро по входному JSON и о кабинете
не знает — это его граница, а не недосмотр. Перевод живёт целиком в кабинете:
таблица `perevody`, остатки счетов, сумма на экране «Сегодня». Проверять его
там нечем, поэтому прогон отдельный.

Инварианты:
  1. НЕ УЧЁТ. Отметка перевода не меняет журнал операций ни на одну запись.
     Перевод между своими счетами — не выручка и не затрата; попади он в журнал,
     один рубль посчитался бы дважды.
  2. СУММА. «К переводу» = записанный доход × доля накопления − уже переведённое.
  3. ГАШЕНИЕ. После отметки на полную сумму «к переводу» равно нулю.
  4. ДЕНЬГИ НЕ ПРОПАДАЮТ. Накопительный вырос ровно на сумму, источник на неё же
     уменьшился; сумма остатков всех счетов не изменилась.
  5. ОТКАТ. Отмена возвращает и остатки, и «к переводу» к исходным значениям.
  6. ПЕРЕПЛАТА. Отметка сверх нужного не уводит «к переводу» в минус, а
     показывается отдельным числом «с запасом».

Запуск:
    python3 proverka-perevoda.py [--tiho]
Код возврата: 0 — чисто, 1 — есть нарушения.
"""
import argparse
import importlib.util
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def zagruzit(vremennaya_baza):
    import baza
    baza.DB = vremennaya_baza
    spec = importlib.util.spec_from_file_location("srv", os.path.join(HERE, "server.py"))
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    return baza, srv


def prognat(tiho=False):
    narusheniya = []

    def trebuem(uslovie, chto):
        if not uslovie:
            narusheniya.append(chto)
        elif not tiho:
            print("  ок:", chto)

    papka = tempfile.mkdtemp(prefix="proverka-perevoda-")
    try:
        vrem = os.path.join(papka, "uchet.db")
        if os.path.exists(os.path.join(HERE, "uchet.db")):
            shutil.copy(os.path.join(HERE, "uchet.db"), vrem)
        baza, srv = zagruzit(vrem)
        con = baza.soedinit()

        # Свой месяц с заведомо ненулевым накоплением: на живой базе доход может
        # не дотягивать до обязательного, и тогда норма ноль — проверять нечего.
        gm = "2099-07"
        if not baza.sostav(con):
            baza.sohranit_sostav(con, [{"imya": "А", "vozrast": 40, "rodstvo": "отец"},
                                       {"imya": "Б", "vozrast": 38, "rodstvo": "мать"}])
        # Счета заводим свои и всегда: прогон идёт по копии базы, а сверять
        # остатки надо на известных числах, а не на том, что оказалось в семье.
        baza.sohranit_scheta(con, [
            {"nazvanie": "Текущий", "vid": "tekushchiy", "ostatok": 5000},
            {"nazvanie": "Накопительный", "vid": "nakopitelnyy", "ostatok": 0,
             "nakoplenie": 1}])
        baza.sohranit_plan(con, gm, {"А": 4000.0}, {"еда": 1000.0})
        for summa in (2500.0, 1500.0):
            baza.dobavit_operaciyu(con, {"data": gm + "-05", "tip": "vneshnyaya",
                                         "uchastnik": "А", "summa": summa,
                                         "istochnik": "извне семьи", "sozdal": "прогон"})

        def scheta_summa():
            return round(sum(s["ostatok"] for s in baza.scheta(con)), 2)

        def ostatok(sid):
            return next((s["ostatok"] for s in baza.scheta(con) if s["id"] == sid), None)

        nak = baza.schet_nakopleniya(con)
        ist = next(s for s in baza.scheta(con) if not s["nakoplenie"])
        operaciy_do = len(baza.operacii_mesyaca(con, gm))
        vsego_do = scheta_summa()
        nak_do, tek_do = ostatok(nak["id"]), ostatok(ist["id"])

        r = srv.nedelnyy_perevod(con, gm, segodnya=gm + "-20")
        summa = r["k_perevodu"]
        trebuem(summa > 0, "на подготовленном месяце есть что переводить (%.2f)" % summa)
        trebuem(abs(r["dohod_zapisan"] * r["dolya"] - r["nado_vsego"]) < 0.01,
                "И-2: сумма = доход × доля")

        pid = baza.dobavit_perevod(con, {"data": gm + "-20", "summa": summa,
                                         "so_scheta": ist["id"], "na_schet": nak["id"],
                                         "sozdal": "прогон"})

        trebuem(len(baza.operacii_mesyaca(con, gm)) == operaciy_do,
                "И-1: журнал операций не тронут")
        posle = srv.nedelnyy_perevod(con, gm, segodnya=gm + "-20")
        trebuem(posle["k_perevodu"] == 0.0, "И-3: после отметки переводить нечего")
        trebuem(abs(ostatok(nak["id"]) - (nak_do + summa)) < 0.005,
                "И-4: накопительный вырос ровно на сумму")
        trebuem(abs(ostatok(ist["id"]) - (tek_do - summa)) < 0.005,
                "И-4: источник уменьшился ровно на сумму")
        trebuem(abs(scheta_summa() - vsego_do) < 0.005,
                "И-4: сумма по всем счетам не изменилась")

        pid2 = baza.dobavit_perevod(con, {"data": gm + "-21", "summa": 100.0,
                                          "so_scheta": ist["id"], "na_schet": nak["id"]})
        perepl = srv.nedelnyy_perevod(con, gm, segodnya=gm + "-21")
        trebuem(perepl["k_perevodu"] == 0.0 and abs(perepl["s_zapasom"] - 100.0) < 0.005,
                "И-6: переплата видна отдельно и не уводит сумму в минус")

        baza.udalit_perevod(con, pid2)
        baza.udalit_perevod(con, pid)
        otkat = srv.nedelnyy_perevod(con, gm, segodnya=gm + "-20")
        trebuem(abs(otkat["k_perevodu"] - summa) < 0.005, "И-5: отмена вернула сумму к переводу")
        trebuem(abs(ostatok(nak["id"]) - nak_do) < 0.005 and
                abs(ostatok(ist["id"]) - tek_do) < 0.005, "И-5: отмена вернула остатки")
        trebuem(len(baza.operacii_mesyaca(con, gm)) == operaciy_do,
                "И-1: журнал операций не тронут и после отмены")
        con.close()
    finally:
        shutil.rmtree(papka, ignore_errors=True)
    return narusheniya


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiho", action="store_true")
    a = ap.parse_args()
    n = prognat(a.tiho)
    if n:
        print("\nНАРУШЕНИЯ (%d):" % len(n))
        for x in n:
            print("  ✗", x)
        return 1
    print("\nчисто: перевод не трогает учёт, гасится отметкой и полностью откатывается")
    return 0


if __name__ == "__main__":
    sys.exit(main())
