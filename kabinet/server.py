#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Кабинет семейного учёта — локальный сервис для домашнего ПК.

Что это. Тот же приём, что в /root/transfer/transfer.py: чистая стандартная
библиотека, ноль зависимостей, старт меньше секунды. Запускается на домашнем
компьютере, открывается в браузере. Наружу ничего не смотрит.

  python server.py [--port 8090] [--host 127.0.0.1] [--otkryt]

  --host 0.0.0.0  — пустить телефоны из домашней сети (нужно разрешение
                    в брандмауэре Windows). По умолчанию только этот компьютер.

Устройство:
  baza.py       журнал фактов (SQLite): кто, когда, за что, сколько
  ../yadro/     расчётное ядро: правила распределения, из журнала считается месяц
  ekrany/       экраны: дашборд, ввод, состав семьи

Данные никуда не отправляются: всё лежит в uchet.db рядом со скриптом.
"""
import argparse
import importlib.util
import io
import json
import os
import sys
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
YADRO = os.path.abspath(os.path.join(HERE, "..", "yadro"))
sys.path.insert(0, HERE)
import baza  # noqa: E402

_spec = importlib.util.spec_from_file_location("raschet", os.path.join(YADRO, "raschet.py"))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

IMENA_FONDOV = {"investicii": "Инвестиционный фонд", "zemelnye_pai": "Дивиденды на земельные паи",
                "upravlenie": "Доля управления", "zdravoohranenie": "Фонд здравоохранения",
                "obrazovanie": "Фонд образования", "rezerv": "Резервный фонд",
                "strahovoy": "Страховой фонд"}


def parametry():
    with io.open(os.path.join(YADRO, "parametry.json"), encoding="utf-8") as f:
        return json.load(f)


def sobrat_mesyac(con, gm):
    """Журнал фактов → структура месяца для ядра.

    Ядро не знает про базу и не должно знать: оно принимает тот же словарь, что
    раньше приходил из mesyac.json. Так расчёт остаётся проверяемым отдельно.
    """
    lyudi = baza.sostav(con)
    imena = [p["imya"] for p in lyudi]
    sover = parametry().get("sovershennoletie", 18)
    deti = [p["imya"] for p in lyudi if (p["vozrast"] or 0) < sover]
    neizvestnye = {}
    otdelno = [p["imya"] for p in lyudi if p.get("otdelno")]
    mes = {"_": "собрано из журнала кабинета за %s" % gm,
           "uchastniki": imena, "deti": deti, "otdelno": otdelno,
           "vneshnyaya_vyruchka": {}, "obshchie_zatraty": {},
           "lichnye_zatraty": {}, "vnutrennie_pokupki": []}
    for o in baza.operacii_mesyaca(con, gm):
        t, s = o["tip"], float(o["summa"])
        # Имя в операции может не совпасть с составом (переименовали, опечатались).
        # Молча выкинуть — значит потерять деньги из расчёта, поэтому копим отдельно.
        for pole in ("uchastnik", "istochnik"):
            kto = (o[pole] or "").strip()
            if kto and t not in ("zatrata",) and kto not in imena and \
               not (pole == "istochnik" and (t in ("vneshnyaya", "iz_fonda"))):
                neizvestnye[kto] = neizvestnye.get(kto, 0.0) + s
        if t == "vneshnyaya":
            mes["vneshnyaya_vyruchka"][o["uchastnik"]] = \
                mes["vneshnyaya_vyruchka"].get(o["uchastnik"], 0.0) + s
        elif t == "zatrata":
            statya = o["statya"] or "прочее"
            mes["obshchie_zatraty"][statya] = mes["obshchie_zatraty"].get(statya, 0.0) + s
        elif t == "lichnaya":
            # Личная трата ложится НА ТОГО, кто потратил, и только на него.
            # Ядро это умеет с самого начала, а кабинет всегда передавал пустой
            # словарь — поэтому мороженое приходилось записывать «обязательной
            # тратой», и тогда часть чужой покупки ложилась на других, а
            # прожиточный минимум рос и снижал норму накопления всей семье.
            kto = o["uchastnik"]
            mes["lichnye_zatraty"][kto] = mes["lichnye_zatraty"].get(kto, 0.0) + s
        elif t == "vnutrennyaya":
            mes["vnutrennie_pokupki"].append(
                {"komu": o["uchastnik"], "chto": o["statya"] or "работа",
                 "ot_uchastnika": o["istochnik"], "summa": s})
        elif t == "iz_fonda":
            mes["vnutrennie_pokupki"].append(
                {"komu": o["uchastnik"], "chto": o["statya"] or "выплата",
                 "iz_fonda": o["istochnik"], "summa": s})
    mes["_neizvestnye"] = neizvestnye
    return mes


def _nak_iz_vyruchki(u):
    """Сколько выручки месяца ушло в накопление — по факту, а не по формуле.

    Берётся приход всех фондов за вычетом дохода, начисленного на их же остатки:
    тот пришёл от капитала, а не из выручки. Включает доплату до минимума взноса,
    которой формула «выручка × норма» не видела вовсе.
    """
    return (sum(u.fondy.prihod.values())
            - sum(u.dohod_fondam.values())
            # Погашение долга перекладывает деньги из инвестиционного фонда в
            # резервный. Это не выручка: без вычета «накопление за месяц»
            # завышалось ровно на погашенную сумму, а потребление занижалось.
            - getattr(u, "mezhdu_fondami", 0.0))


def scheta_svodka(con):
    """Сколько денег есть на самом деле — и сходится ли это с учётом.

    Учёт знает, сколько НАКОПЛЕНО: имущественные паи всех месяцев плюс остатки
    фондов последнего посчитанного месяца. Счета знают, сколько лежит. Разница —
    это либо личные деньги на руках, либо непроведённая операция.
    """
    spisok = baza.scheta(con)
    vsego = sum(s["ostatok"] for s in spisok)
    nak_scheta = sum(s["ostatok"] for s in spisok if s["nakoplenie"])
    v_god = sum(s["ostatok"] * s["stavka"] for s in spisok)
    # Месяцы сцеплены: итог последнего уже включает всё накопленное раньше.
    # Складывать приросты нельзя — будет двойной счёт. Раньше складывали приросты,
    # а остаток фондов брали только последнего месяца, и остатки прошлых пропадали.
    pai, fondy_ost, karm_ost, mesyacev = 0.0, 0.0, 0.0, 0
    bal = None
    for gm in sorted(baza.mesyacy(con)):
        r = schitat_mesyac(con, gm, _bal=bal)
        if r.get("ok"):
            pai = r["pai_vsego"]
            fondy_ost = r["ostatok_fondov"]
            karm_ost = r.get("karmany_ostatok", 0.0)
            bal = r.get("_balans_konca")
            mesyacev += 1
    uchet = pai + fondy_ost + karm_ost
    return {"ok": True, "scheta": spisok, "vsego": round(vsego, 2),
            "nakopitelnye": round(nak_scheta, 2),
            "dohod_v_god": round(v_god, 2), "dohod_v_mesyac": round(v_god / 12, 2),
            "uchet_nakopleno": round(uchet, 2), "uchet_pai": round(pai, 2),
            "uchet_fondy": round(fondy_ost, 2), "uchet_karmany": round(karm_ost, 2),
            "mesyacev": mesyacev,
            "rashozhdenie": round(nak_scheta - uchet, 2)}


def raspredelenie(con, gm):
    """Три доли, на которые делится КАЖДОЕ поступление месяца (вариант Б).

    Считается от ПЛАНА, а не от факта: иначе первую неделю делить нечем — норма
    прогрессивная и зависит от месячного итога. План берём консервативный, тогда
    корректировка при закрытии идёт в приятную сторону: довнести в фонды.
    """
    par = parametry()
    pl = baza.plan_mesyaca(con, gm)
    dohod = sum(pl["postupleniya"].values())
    karmany = par.get("karmany", {})
    minimum = sum(pl["zatraty"].values()) + sum(karmany.values())
    if dohod <= 0:
        return {"ok": False, "err": "на этот месяц не заведён план поступлений",
                "plan_dohod": 0, "plan_minimum": round(minimum, 2)}
    norma = R.norma_po_dohodu(par, dohod, minimum) if minimum > 0 else \
            R.norma_po_dohodu(par, dohod, par.get("prozhitochnyy_minimum", {}).get("summa"))
    dolya_obyaz = min(1.0, minimum / dohod)
    dolya_nak = norma
    dolya_svob = 1.0 - dolya_obyaz - dolya_nak
    return {"ok": True, "plan_dohod": round(dohod, 2), "plan_minimum": round(minimum, 2),
            "norma": round(norma, 4),
            "dolya_nakoplenie": round(dolya_nak, 4),
            "dolya_obyazatelnoe": round(dolya_obyaz, 4),
            "dolya_svobodnoe": round(dolya_svob, 4),
            "hvatit": dolya_svob >= -1e-9,
            "postupleniya": pl["postupleniya"], "zatraty": pl["zatraty"],
            # Карманы входят в прожиточный минимум, но в таблицу затрат не попадали —
            # панель показывала 1 100 при строках на 1 000, и разница ничем не объяснялась.
            "karmany": {k: round(float(v), 2) for k, v in karmany.items()},
            "zatraty_summa": round(sum(pl["zatraty"].values()), 2),
            "karmany_summa": round(sum(karmany.values()), 2)}


def _balans_iz_ucheta(u, par):
    """Баланс на следующий месяц. Сборка одна на всех — в ядре (R.balans_na_sleduyushchiy).

    Своя копия здесь была ошибкой: набор полей разошёлся с yadro/perenos.py,
    и месяц из журнала переносился не так, как месяц из файла.
    """
    return R.balans_na_sleduyushchiy(u, par)


def _balans_do(con, gm):
    """Остатки на начало месяца gm: прогнать все предыдущие месяцы по порядку.

    Раньше кабинет не переносил остатки вовсе — каждый месяц считался с нуля,
    а экраны при этом подписывали «переходит на следующий месяц». Из-за этого
    терялись остатки фондов и карманов, долг резерву всегда был нулём, и правило
    «четвёртый дефицитный месяц подряд останавливает расчёт» не могло сработать.
    """
    bal, sloman = None, []
    for m in sorted(baza.mesyacy(con)):
        if m >= gm:
            break
        r = schitat_mesyac(con, m, _bal=bal)
        if r.get("ok"):
            bal = r.get("_balans_konca")
        else:
            # Раньше упавший месяц молча выпадал, и следующий получал устаревший
            # баланс: целый месяц накопления исчезал без единого слова на экране.
            sloman.append((m, r.get("err", "не посчитался")))
    return bal, sloman


def schitat_mesyac(con, gm, _bal=None):
    par = parametry()
    # Получателя доли управления берём из состава семьи, а не из parametry.json:
    # там лежит имя «мама», и на другом составе деньги уходили в никуда.
    hoz = [p["imya"] for p in baza.sostav(con) if p.get("hozyaystvo")]
    par = dict(par)
    par["poluchatel_upravleniya"] = hoz[0] if hoz else None
    mes = sobrat_mesyac(con, gm)
    if not mes["uchastniki"]:
        return {"ok": False, "err": "состав семьи не заведён"}
    # Остатки предыдущих месяцев. _bal передан — цепочка уже построена выше.
    sloman = []
    if _bal is not None:
        bal = _bal
    else:
        bal, sloman = _balans_do(con, gm)
    if bal:
        mes["balans"] = bal
    if not mes["vneshnyaya_vyruchka"]:
        e = "за месяц нет ни одного поступления извне"
        if mes.get("_neizvestnye"):
            e += ". В журнале есть записи на людей, которых нет в составе: " + \
                 ", ".join(sorted(mes["_neizvestnye"])) + " — поправьте состав или записи"
        return {"ok": False, "err": e}
    try:
        u = R.schitat(par, mes)
    except SystemExit as e:
        return {"ok": False, "err": str(e), "ostanovka": True}
    lich = {k: R._lichnyy(u, k) for k in u.uchastniki}
    vyr = sum(u.vyruchka.values())
    pai_vsego = sum(u.imushch_pai.values())
    pai_vhod = sum(u.pai_vhod.values())
    lyudi_po_imeni = {p["imya"]: p for p in baza.sostav(con)}
    IST = {"vneshnyaya": "извне семьи", "iz_fonda": "фонд", "vnutrennyaya": "от участника"}
    ops = []
    for o in reversed(baza.operacii_mesyaca(con, gm)):
        if o["tip"] == "zatrata":
            continue
        ist = o["istochnik"] or ""
        ops.append({"date": o["data"][8:] + "." + o["data"][5:7],
                    "to": o["uchastnik"], "what": o["statya"] or "—",
                    "from": IMENA_FONDOV.get(ist, ist),
                    "sum": round(float(o["summa"]), 2),
                    "vneshniy": o["tip"] == "vneshnyaya"})
    fondy = []
    for f, imya in IMENA_FONDOV.items():
        # Приход и расход берутся из самоучёта ядра, а не считаются заново.
        # Прежняя формула «выручка × норматив» не видела ни доплаты минимума,
        # ни покрытия дефицита, а max(0.0, …) прятал отрицательный расход —
        # из-за этого таблица не сходилась сама с собой.
        prihod = u.fondy.prihod[f]
        vhod = u.fondy_vhod[f]
        fondy.append({"key": f, "name": imya, "norm": round(u.nak_doli.get(f, 0.0), 4),
                      "prihod": round(prihod, 2),
                      "rashod": round(u.fondy.rashod[f], 2),
                      "vhod": round(vhod, 2),
                      "ostatok": round(u.fondy[f], 2),
                      "dohod": round(u.dohod_fondam[f], 2),
                      "stub": f in ("zdravoohranenie", "obrazovanie", "rezerv", "strahovoy")})
    return {
        "ok": True, "mesyac": gm,
        "prozhitochnyy_minimum": round(u.prozh_minimum, 2),
        "norma": round(u.norma, 4),
        "vneshniy_dohod": round(u.vneshniy_pritok, 2),
        "vnutrenniy_oborot": round(u.vnutrenniy_oborot, 2),
        "lichnyy_dohod": round(sum(lich.values()), 2),
        # Прирост ЗА МЕСЯЦ и накопленное ВСЕГО — разные числа. Пока остатки не
        # переносились, они совпадали, и «Счета» складывали приросты всех месяцев,
        # что после сцепления месяцев дало бы двойной счёт.
        "prirost_paev": round(pai_vsego - pai_vhod, 2),
        "pai_vsego": round(pai_vsego, 2),
        "ostatok_fondov": round(sum(u.fondy.values()), 2),
        # Изменение ЗА МЕСЯЦ. Сверка месяца обязана складывать месячные числа:
        # накопленный остаток фондов давал разрыв в тысячи рублей на экране.
        "izmenenie_fondov": round(sum(u.fondy.values()) - sum(u.fondy_vhod.values()), 2),
        "karmany_ostatok": round(sum(u.karmany.values()), 2),
        "izmenenie_karmanov": round(sum(u.karmany.values()) - sum(u.karmany_vhod.values()), 2),
        "naruzhu": round(u.vneshniy_rashod, 2),
        "zatraty": round(sum(u.zatraty.values()), 2),
        "deficit": round(u.deficit, 2), "nepokryto": round(u.nepokryto, 2),
        "dolg": round(u.dolg, 2), "mes_deficita": u.mes_deficita,
        "vyruchka_vsego": round(vyr, 2),
        # Сколько на самом деле ушло в накопление за месяц: приход всех фондов
        # минус доход на их же остатки (он не из выручки). Прежнее «выручка × норма»
        # не учитывало доплату минимума и расходилось с ядром в бедный месяц.
        "nakoplenie_vsego": round(_nak_iz_vyruchki(u), 2),
        "potreblenie_vsego": round(vyr - _nak_iz_vyruchki(u), 2),
        # Сколько накопленного осталось на конец месяца. Это ДРУГОЕ число:
        # часть прихода уже роздана дивидендами, долей управления и покупками.
        "nakopleno_ostalos": round(sum(u.imushch_pai.values()) + sum(u.fondy.values()), 2),
        "rules": "нормативы: " + parametry().get("_versiya", ""),
        "neizvestnye": mes.get("_neizvestnye", {}),
        "ops": ops, "fondy_polnye": fondy,
        "_balans_konca": _balans_iz_ucheta(u, par),
        # Разрыв цепочки виден на экране: без этого остатки упавшего месяца
        # исчезали, а месяц выглядел нормально посчитанным.
        "cepochka_rvana": [{"mesyac": m, "err": e} for m, e in sloman],
        "deficit_blok": {"deficit": round(u.deficit, 2), "nepokryto": round(u.nepokryto, 2),
                         "dolg": round(u.dolg, 2), "mes_deficita": u.mes_deficita},
        "people": [{"key": k, "name": k, "slot": i + 1,
                    "otdelno": bool(lyudi_po_imeni.get(k, {}).get("otdelno")),
                    "rebenok": k in mes["deti"],
                    "vyruchka": round(u.vyruchka[k], 2),
                    "na_ruki": round(lich[k], 2),
                    "pay": round(u.imushch_pai[k], 2),
                    "pomoshch": round(u.pokrytie_komu[k], 2),
                    "nedobor": round(u.min_nedobor[k], 2)}
                   for i, k in enumerate(u.uchastniki)],
        "fondy": [{"key": f, "name": IMENA_FONDOV.get(f, f),
                   "ostatok": round(u.fondy[f], 2),
                   "dohod": round(u.dohod_fondam[f], 2)}
                  for f in IMENA_FONDOV if abs(u.fondy[f]) > 0.005],
        # Карманы (регулярное отчисление на нерегулярное — одежда детям и т.п.)
        # входят в прожиточный минимум, поэтому обязаны стоять и в строках затрат.
        # Без них таблица давала 1300 против 1400 в шапке того же экрана.
        "zatraty_stroki": [{"name": k, "sum": round(v, 2), "karman": False}
                           for k, v in mes["obshchie_zatraty"].items()] +
                          [{"name": k, "sum": round(v, 2), "karman": True}
                           for k, v in parametry().get("karmany", {}).items()],
        "plan": plan_fakt(con, gm, u, mes),
        "preduprezhdeniya": list(u.preduprezhdeniya),
    }


PRAVILA_ZATRAT = [
    ("po_dohodu_vzroslyh",           "По доходу взрослых, живущих с семьёй",
     "Как сейчас. Кто больше может — тот больше платит; дети и живущие отдельно не платят."),
    ("porovnu_krome_detey",          "Поровну между взрослыми",
     "Буква Р-4. Взрослый без выручки уходит в глубокий минус."),
    ("po_dole_vyruchki_krome_detey", "По доле выручки, только взрослые",
     "Платит тот, кто заработал, пропорционально заработку."),
    ("porovnu",                      "Поровну между всеми, включая детей",
     "Дети перестают быть в плюсе на бедном месяце, но платят наравне со взрослыми."),
    ("po_dole_vyruchki",             "По доле выручки среди всех",
     "Дети платят пропорционально тому, что заработали внутри семьи."),
]


def _svodka_yadra(u, R_, gm="что-если"):
    """Из состояния ядра — те же поля, что отдаёт /api/mesyac.

    Отдельной функцией, потому что песочница обязана считаться ТЕМ ЖЕ ядром и
    показывать ТЕ ЖЕ величины, иначе она не проверка, а вторая модель.
    """
    lich = {k: R_._lichnyy(u, k) for k in u.uchastniki}
    vyr = sum(u.vyruchka.values())
    return {
        "prozhitochnyy_minimum": round(u.prozh_minimum, 2),
        "norma": round(u.norma, 4),
        "vyruchka_vsego": round(vyr, 2),
        "vnutrenniy_oborot": round(u.vnutrenniy_oborot, 2),
        # Главное деление по Чартаеву: вся выручка семьи делится на фонд
        # накопления и фонд потребления. Берутся из движения фондов ядра —
        # «выручка × норма» не видит доплату минимума и врёт в бедный месяц.
        "nakoplenie_fond": round(_nak_iz_vyruchki(u), 2),
        "potreblenie_fond": round(vyr - _nak_iz_vyruchki(u), 2),
        # Семь субсчетов фонда накопления: их сумма равна фонду накопления.
        "subscheta": [{"key": f, "name": IMENA_FONDOV.get(f, f),
                       "dolya": round(u.nak_doli.get(f, 0.0), 4),
                       "summa": round(u.fondy.prihod[f] - u.dohod_fondam[f], 2)}
                      for f in IMENA_FONDOV],
        "vneshniy_dohod": round(u.vneshniy_pritok, 2),
        "lichnyy_dohod": round(sum(lich.values()), 2),
        "prirost_paev": round(sum(u.imushch_pai.values()), 2),
        "ostatok_fondov": round(sum(u.fondy.values()), 2),
        "zatraty": round(sum(u.zatraty.values()), 2),
        "nakoplenie_vsego": round(_nak_iz_vyruchki(u), 2),
        "nakopleno_ostalos": round(sum(u.imushch_pai.values()) + sum(u.fondy.values()), 2),
        "deficit_blok": {"deficit": round(u.deficit, 2),
                         "nepokryto": round(u.nepokryto, 2),
                         "dolg": round(u.dolg, 2),
                         "mes_deficita": u.mes_deficita},
        "people": [{"key": k, "name": k, "slot": i + 1,
                    "vyruchka": round(u.vyruchka[k], 2),
                    "na_ruki": round(lich[k], 2),
                    "pay": round(u.imushch_pai[k], 2),
                    "zatraty": round(u.zatraty[k], 2),
                    "pomoshch": round(u.pokrytie_komu[k], 2),
                    "nedobor": round(u.min_nedobor[k], 2)}
                   for i, k in enumerate(u.uchastniki)],
    }


def _mesyac_iz_zaprosa(con, z):
    """Выдуманный месяц из полей формы. В базу не попадает ничего."""
    lyudi = baza.sostav(con)
    imena = [p["imya"] for p in lyudi]
    par = parametry()
    sover = par.get("sovershennoletie", 18)
    vozrast = {p["imya"]: (p["vozrast"] or 0) for p in lyudi}
    # Возраст и «живёт отдельно» можно переопределить в песочнице: это два
    # открытых допущения прототипа, и проверять их надо тоже здесь.
    pereopr_otd = z.get("otdelno")
    otdelno = list(pereopr_otd) if isinstance(pereopr_otd, list) else \
        [p["imya"] for p in lyudi if p.get("otdelno")]
    deti = [k for k in imena if vozrast.get(k, 0) < sover]
    mes = {"_": "песочница", "uchastniki": imena, "deti": deti, "otdelno": otdelno,
           "vneshnyaya_vyruchka": {}, "obshchie_zatraty": {},
           "lichnye_zatraty": {}, "vnutrennie_pokupki": []}
    for k, v in (z.get("vneshnyaya") or {}).items():
        v = float(v or 0)
        if k in imena and v:
            mes["vneshnyaya_vyruchka"][k] = v
    for k, v in (z.get("zatraty") or {}).items():
        v = float(v or 0)
        if v:
            mes["obshchie_zatraty"][k] = v
    # Личные траты в песочнице — по участникам, тем же ключом, что в ядре.
    for k, v in (z.get("lichnye") or {}).items():
        v = float(v or 0)
        if k in imena and v:
            mes["lichnye_zatraty"][k] = mes["lichnye_zatraty"].get(k, 0.0) + v
    # Кто платит внутри семьи: тот, у кого больше внешней выручки. В семье это
    # всегда так и есть, а выбирать плательщика вручную — лишнее поле в форме.
    platelshchik = max(mes["vneshnyaya_vyruchka"], key=mes["vneshnyaya_vyruchka"].get) \
        if mes["vneshnyaya_vyruchka"] else (imena[0] if imena else None)
    for p in (z.get("vnutrennie") or []):
        summa = float(p.get("summa") or 0)
        komu = p.get("komu")
        if summa and komu in imena and komu != platelshchik:
            mes["vnutrennie_pokupki"].append(
                {"komu": komu, "chto": p.get("chto") or "работа по дому",
                 "ot_uchastnika": platelshchik, "summa": summa})
    return mes, platelshchik


def chto_esli(con, z):
    """Песочница: тот же расчёт на выдуманных числах, без записи в базу."""
    mes, platelshchik = _mesyac_iz_zaprosa(con, z)
    if not mes["uchastniki"]:
        return {"ok": False, "err": "состав семьи не заведён — песочнице некого считать"}
    if not mes["vneshnyaya_vyruchka"]:
        return {"ok": False, "err": "поставьте хотя бы одно поступление извне"}

    bazovye = parametry()
    hoz = [p["imya"] for p in baza.sostav(con) if p.get("hozyaystvo")]

    def par_dlya(pravilo):
        par = dict(bazovye)
        par["obshchie_zatraty_delyatsya"] = pravilo
        par["poluchatel_upravleniya"] = hoz[0] if hoz else None
        if "karmany" in z:
            par["karmany"] = {k: float(v) for k, v in (z["karmany"] or {}).items()
                              if float(v or 0)}
        if z.get("norma_polnaya") is not None:
            # Ползунок обязан двигать ТРИ вещи разом, иначе половинки перестают
            # складываться в единицу и расчёт печатает деньги из воздуха:
            # полную норму, верхнюю ступень шкалы и доли субсчетов.
            nov = float(z["norma_polnaya"])
            if not 0.0 <= nov <= 1.0:
                raise ValueError("полная норма накопления должна быть от 0 до 1")
            staraya = float(par["norma_nakopleniya"]["polnaya_norma"])
            nn = json.loads(json.dumps(par["norma_nakopleniya"]))
            nn["polnaya_norma"] = nov
            # Масштабируем ВСЕ ступени, а не одну верхнюю. Иначе на доходе ниже
            # верхней ступени ползунок мёртв, а на нуле норма остаётся 45 %,
            # тогда как в фонды не идёт ничего — и деньги исчезают.
            if nn.get("stupeni") and staraya > 0:
                k = nov / staraya
                for st in nn["stupeni"]:
                    st["dolya"] = min(1.0, max(0.0, st["dolya"] * k))
            elif nn.get("stupeni"):
                for st in nn["stupeni"]:
                    st["dolya"] = 0.0
                if nn["stupeni"]:
                    nn["stupeni"][-1]["dolya"] = nov
            par["norma_nakopleniya"] = nn
            if staraya > 0:
                par["nakoplenie"] = {f: d * nov / staraya
                                     for f, d in par["nakoplenie"].items()}
            else:
                par["nakoplenie"] = {f: nov / len(par["nakoplenie"])
                                     for f in par["nakoplenie"]}
        return par

    def poschitat(pravilo):
        try:
            u = R.schitat(par_dlya(pravilo), mes)
        except SystemExit as e:
            return {"ok": False, "err": str(e)}
        except Exception as e:                     # песочница не должна ронять кабинет
            return {"ok": False, "err": "расчёт не сошёлся: %s" % e}
        r = _svodka_yadra(u, R)
        r["ok"] = True
        return r

    vybrannoe = z.get("pravilo") or bazovye.get("obshchie_zatraty_delyatsya")
    osnovnoy = poschitat(vybrannoe)
    # Пять правил на одних и тех же числах — то, чем закрывается открытый вопрос Р-4.
    sravnenie = []
    for klyuch, imya, poyasnenie in PRAVILA_ZATRAT:
        r = poschitat(klyuch)
        sravnenie.append({"klyuch": klyuch, "imya": imya, "poyasnenie": poyasnenie,
                          "ok": r.get("ok", False), "err": r.get("err"),
                          "na_ruki": (r.get("people") if r.get("ok") else None),
                          "deficit": (r["deficit_blok"]["nepokryto"] if r.get("ok") else None)})
    return {"ok": osnovnoy.get("ok", False), "err": osnovnoy.get("err"),
            "pravilo": vybrannoe, "platelshchik": platelshchik,
            "itog": osnovnoy if osnovnoy.get("ok") else None,
            "sravnenie": sravnenie,
            "pravila": [{"klyuch": k, "imya": i, "poyasnenie": p}
                        for k, i, p in PRAVILA_ZATRAT]}


def zagotovka_pesochnitsy(con, gm=""):
    """Чем заполнить форму при открытии: настоящий месяц как отправная точка.

    Крутить проще от того, что было, чем от нуля: видно, какое именно изменение
    что сдвинуло.
    """
    gm = gm or date.today().strftime("%Y-%m")
    if gm not in baza.mesyacy(con):
        mm = baza.mesyacy(con)
        gm = mm[-1] if mm else gm
    par = parametry()
    sover = par.get("sovershennoletie", 18)
    lyudi = baza.sostav(con)
    mes = sobrat_mesyac(con, gm)
    platyat = {}
    for p in mes["vnutrennie_pokupki"]:
        if "ot_uchastnika" in p:
            platyat[p["komu"]] = platyat.get(p["komu"], 0.0) + float(p["summa"])
    chto = {}
    for p in mes["vnutrennie_pokupki"]:
        if "ot_uchastnika" in p and p["komu"] not in chto:
            chto[p["komu"]] = p.get("chto") or ""
    return {
        "ok": True, "mesyac": gm,
        "lyudi": [{"imya": p["imya"], "vozrast": p["vozrast"] or 0,
                   "rodstvo": p["rodstvo"] or "",
                   "rebenok": (p["vozrast"] or 0) < sover,
                   "otdelno": bool(p.get("otdelno")),
                   "vneshnyaya": round(mes["vneshnyaya_vyruchka"].get(p["imya"], 0.0), 2),
                   "vnutri": round(platyat.get(p["imya"], 0.0), 2),
                   "chto": chto.get(p["imya"], "")}
                  for p in lyudi],
        "zatraty": {k: round(v, 2) for k, v in mes["obshchie_zatraty"].items()},
        "karmany": dict(par.get("karmany", {})),
        "pravilo": par.get("obshchie_zatraty_delyatsya"),
        "norma_polnaya": par.get("norma_nakopleniya", {}).get("polnaya_norma", 0.5),
        "sovershennoletie": sover,
        "pravila": [{"klyuch": k, "imya": i, "poyasnenie": p}
                    for k, i, p in PRAVILA_ZATRAT],
    }


def istoriya(con):
    """Сводка по всем месяцам журнала: доход, на руки, прирост пая, накопление.

    Нужна ровно для одного — показать, растёт пай или стоит. Система Чартаева
    окупается годами накопления, и это единственное, чего не видно на месяце.
    Считается тем же ядром, что и месяц: правила в одном месте.
    """
    out = []
    bal = None
    # Баланс передаём по цепочке. Без этого каждый месяц заново прогонял всю
    # предысторию: 36 месяцев считались 1.8 с вместо 0.16 с, и на десяти годах
    # экран истории вставал.
    for gm in sorted(baza.mesyacy(con)):
        try:
            m = schitat_mesyac(con, gm, _bal=bal)
        except Exception:
            continue
        if not m.get("ok"):
            continue
        bal = m.get("_balans_konca")
        out.append({"mesyac": gm,
                    "vneshniy_dohod": m["vneshniy_dohod"],
                    "na_ruki": m["lichnyy_dohod"],
                    "prirost_paev": m["prirost_paev"],
                    "_pai_vsego": m["pai_vsego"],
                    "nakoplenie": m["nakoplenie_vsego"],
                    "zatraty": m["zatraty"],
                    "deficit": m["deficit"]})
    out.sort(key=lambda r: r["mesyac"])
    for r in out:
        # Итог берём посчитанный ядром, а не копим округлённые приросты:
        # на 36 месяцах накопленная разница с «Счетами» доходила до 0.15 руб.
        r["pai_itogo"] = r.pop("_pai_vsego")
    return {"ok": True, "mesyacy": out}


def plan_fakt(con, gm, u, mes):
    """План против факта и корректировка отчислений при закрытии месяца.

    По плану в фонды уходила доля с каждого поступления. Факт месяца знает
    настоящую норму. Разница — это деньги, которые надо доперевести на
    накопительный счёт (или вернуть в потребление).
    """
    r = raspredelenie(con, gm)
    if not r.get("ok"):
        return {"est": False, "err": r.get("err")}
    fakt_dohod = sum(mes.get("vneshnyaya_vyruchka", {}).values())
    nak_plan = fakt_dohod * r["dolya_nakoplenie"]
    nak_fakt = fakt_dohod * u.norma
    fakt_zatraty = sum(mes.get("obshchie_zatraty", {}).values()) + \
        sum(parametry().get("karmany", {}).values())
    return {"est": True,
            "dohod_plan": r["plan_dohod"], "dohod_fakt": round(fakt_dohod, 2),
            "dohod_otklonenie": round(fakt_dohod - r["plan_dohod"], 2),
            "minimum_plan": r["plan_minimum"], "minimum_fakt": round(fakt_zatraty, 2),
            "minimum_otklonenie": round(fakt_zatraty - r["plan_minimum"], 2),
            "norma_plan": r["norma"], "norma_fakt": round(u.norma, 4),
            "nakoplenie_plan": round(nak_plan, 2), "nakoplenie_fakt": round(nak_fakt, 2),
            "korrektirovka": round(nak_fakt - nak_plan, 2)}


def nedelnyy_perevod(con, gm=None, segodnya=None):
    """Сколько отложить на накопительный счёт прямо сейчас.

    Единственное действие, которого просит ведущая учёт: одно число, когда она
    в приложении банка. Считается не по неделям календаря, а нарастающим итогом:
    что записано с начала месяца × доля накопления, минус уже переведённое.
    Пропустил неделю — число просто больше; перевёл лишнего — уйдёт в минус и
    следующий раз попросит меньше. Границы недели не нужны, и это осознанно:
    норма накопления прогрессивная, её нельзя честно нарезать на семидневки.

    Доля берётся из плана (Р-16, «вариант Б»): норма зависит от месячного итога,
    а он в середине месяца ещё не наступил. Плана нет — считаем по факту и
    говорим об этом: такая доля занижена, при закрытии месяца придётся доложить.
    """
    par = parametry()
    gm = gm or date.today().strftime("%Y-%m")
    segodnya = segodnya or date.today().strftime("%Y-%m-%d")
    ops = baza.operacii_mesyaca(con, gm)
    prihody = [o for o in ops if o["tip"] == "vneshnyaya"]
    dohod = sum(float(o["summa"]) for o in prihody)

    r = raspredelenie(con, gm)
    if r.get("ok"):
        dolya, osnova, pochemu = r["dolya_nakoplenie"], "план", ""
    else:
        zatraty = sum(float(o["summa"]) for o in ops if o["tip"] == "zatrata")
        minimum = zatraty + sum(par.get("karmany", {}).values())
        pm = minimum or par.get("prozhitochnyy_minimum", {}).get("summa")
        dolya = R.norma_po_dohodu(par, dohod, pm)
        osnova, pochemu = "факт", r.get("err", "плана нет")

    nado = dohod * dolya
    perevody = baza.perevody_mesyaca(con, gm)
    perevedeno = sum(float(x["summa"]) for x in perevody)
    k_perevodu = round(nado - perevedeno, 2)

    # С какого дня копилось. Последний перевод внутри месяца — точка отсчёта;
    # его нет — начало месяца. Показать надо именно то, что человек записал
    # после прошлого перевода: это его неделя, а не абстрактный период.
    posl = baza.posledniy_perevod(con, segodnya)
    s_daty = gm + "-01"
    if posl and posl["data"] >= s_daty:
        s_daty = posl["data"]
    novye = [o for o in prihody if o["data"] >= s_daty]
    # Поступления того же дня, что и прошлый перевод, могли быть записаны до него.
    # Различить нечем — берём осторожно: они уже учтены в «переведено».
    schet = baza.schet_nakopleniya(con)
    # Откуда переводить: самый полный из тех, что не хранилище накоплений.
    # Предложение, а не решение — человек знает, из какого банка платил.
    prochie = [x for x in baza.scheta(con) if not x["nakoplenie"]]
    istochnik = max(prochie, key=lambda x: x["ostatok"]) if prochie else None
    hvatit = bool(istochnik) and istochnik["ostatok"] + 1e-9 >= max(0.0, k_perevodu)
    return {"ok": True, "mesyac": gm, "segodnya": segodnya,
            "istochnik": istochnik, "hvatit_na_istochnike": hvatit,
            "dohod_zapisan": round(dohod, 2), "postupleniy": len(prihody),
            "dolya": round(dolya, 4), "osnova": osnova, "pochemu": pochemu,
            "nado_vsego": round(nado, 2), "perevedeno": round(perevedeno, 2),
            "k_perevodu": max(0.0, k_perevodu),
            "s_zapasom": round(-k_perevodu, 2) if k_perevodu < -0.005 else 0.0,
            "s_daty": s_daty, "novyh_postupleniy": len(novye),
            "novyh_na_summu": round(sum(float(o["summa"]) for o in novye), 2),
            "schet": schet, "schet_est": bool(schet),
            "perevody": perevody,
            "mozhno_otmetit": max(0.0, k_perevodu) > 0.005 and bool(schet)}


class Handler(BaseHTTPRequestHandler):
    server_version = "kabinet/1.0"

    def log_message(self, fmt, *a):      # тихо: это домашний ПК, а не сервер
        pass

    # --- ответы ---
    def _json(self, data, code=200):
        telo = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(telo)))
        self.end_headers()
        self.wfile.write(telo)

    PREFIKSY = ("/uchet",)

    def _snyat_prefiks(self):
        """Возвращает префикс запроса и укорачивает self.path на него."""
        for pr in self.PREFIKSY:
            if self.path == pr or self.path.startswith(pr + "/") or \
               self.path.startswith(pr + "?"):
                self.path = self.path[len(pr):] or "/"
                if not self.path.startswith("/"):
                    self.path = "/" + self.path
                return pr
        return ""

    def _fayl(self, imya):
        put = os.path.join(HERE, "ekrany", imya)
        if not os.path.isfile(put):
            return self._json({"err": "нет экрана " + imya}, 404)
        with open(put, "rb") as f:
            telo = f.read()
        pr = getattr(self, "prefiks", "")
        if pr:
            metka = ('<script>window.PREFIX="%s";</script>' % pr).encode("utf-8")
            i = telo.find(b"<head>")
            telo = (telo[:i + 6] + metka + telo[i + 6:]) if i >= 0 else metka + telo
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(telo)))
        self.end_headers()
        self.wfile.write(telo)

    def _telo(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    # --- маршруты ---
    def do_GET(self):
        self.prefiks = self._snyat_prefiks()
        u = urlparse(self.path)
        q = parse_qs(u.query)
        con = baza.soedinit()
        try:
            if u.path == "/favicon.ico":
                # Браузер просит его на каждой странице; без ответа в консоли
                # висит 404 и аудит справедливо ругается.
                self.send_response(204); self.end_headers(); return
            if u.path in ("/", "/segodnya"):
                return self._fayl("segodnya.html")
            if u.path == "/chto-esli":
                return self._fayl("pesochnitsa.html")
            if u.path == "/api/chto-esli/zagotovka":
                return self._json(zagotovka_pesochnitsy(con, q.get("mesyac", [""])[0]))
            if u.path in ("/mesyac", "/dashboard", "/otchet"):
                return self._fayl("mesyac.html")
            if u.path == "/vvod":
                return self._fayl("vvod.html")
            if u.path == "/sostav":
                return self._fayl("nastroyka.html")
            if u.path == "/plan":
                return self._fayl("plan.html")
            if u.path == "/scheta":
                return self._fayl("scheta.html")
            if u.path == "/api/scheta":
                return self._json(scheta_svodka(con))
            if u.path == "/api/perevod":
                return self._json(nedelnyy_perevod(con, q.get("mesyac", [""])[0] or None))
            if u.path == "/api/plan":
                gm = (q.get("mesyac", [""])[0] or date.today().strftime("%Y-%m"))
                pl = baza.plan_mesyaca(con, gm)
                pl["ok"] = True
                pl["predydushchiy"] = baza.predydushchiy_plan(con, gm)
                pl["raspredelenie"] = raspredelenie(con, gm)
                return self._json(pl)
            if u.path == "/api/raspredelenie":
                gm = (q.get("mesyac", [""])[0] or date.today().strftime("%Y-%m"))
                return self._json(raspredelenie(con, gm))
            if u.path == "/api/sostav":
                return self._json({"ok": True, "lyudi": baza.sostav(con)})
            if u.path == "/api/istoriya":
                return self._json(istoriya(con))
            if u.path == "/api/mesyacy":
                spisok = baza.mesyacy(con) or [date.today().strftime("%Y-%m")]
                return self._json({"ok": True, "mesyacy": spisok})
            if u.path == "/api/operacii":
                gm = (q.get("mesyac", [""])[0] or date.today().strftime("%Y-%m"))
                return self._json({"ok": True, "operacii": baza.operacii_mesyaca(con, gm)})
            if u.path == "/api/mesyac":
                gm = (q.get("mesyac", [""])[0] or date.today().strftime("%Y-%m"))
                return self._json(schitat_mesyac(con, gm))
            if u.path == "/api/zdorovie":
                return self._json({"ok": True, "baza": baza.DB})
            return self._json({"err": "нет такой страницы"}, 404)
        finally:
            con.close()

    def do_POST(self):
        self.prefiks = self._snyat_prefiks()
        u = urlparse(self.path)
        con = baza.soedinit()
        try:
            d = self._telo()
            if u.path == "/api/chto-esli":
                return self._json(chto_esli(con, d))     # песочница: ничего не пишет
            if u.path == "/api/operacii":
                if not d.get("summa"):
                    return self._json({"ok": False, "err": "не указана сумма"}, 400)
                oid = baza.dobavit_operaciyu(con, d)
                return self._json({"ok": True, "id": oid})
            if u.path == "/api/operacii/udalit":
                baza.udalit_operaciyu(con, int(d["id"]))
                return self._json({"ok": True})
            if u.path == "/api/sostav":
                baza.sohranit_sostav(con, d.get("lyudi", []))
                return self._json({"ok": True})
            if u.path == "/api/plan":
                baza.sohranit_plan(con, d["mesyac"], d.get("postupleniya", {}),
                                   d.get("zatraty", {}))
                return self._json({"ok": True})
            if u.path == "/api/scheta":
                baza.sohranit_scheta(con, d.get("scheta", []))
                return self._json({"ok": True})
            if u.path == "/api/perevod":
                # Сумму можно поправить руками: банк округляет, человек переводит
                # «ровно 50», и врать учёту об этом не надо.
                raschet = nedelnyy_perevod(con)
                summa = float(d.get("summa") or raschet["k_perevodu"])
                if summa <= 0:
                    return self._json({"ok": False, "err": "переводить нечего"}, 400)
                schet = raschet.get("schet")
                if not d.get("na_schet") and not schet:
                    return self._json({"ok": False, "err": "не отмечен ни один счёт "
                                       "как хранилище накоплений — откройте «Счета»"}, 400)
                pid = baza.dobavit_perevod(con, {
                    "data": d.get("data") or date.today().strftime("%Y-%m-%d"),
                    "summa": summa, "so_scheta": d.get("so_scheta"),
                    "na_schet": d.get("na_schet") or (schet or {}).get("id"),
                    "kommentariy": d.get("kommentariy", ""),
                    "sozdal": d.get("sozdal", "")})
                return self._json({"ok": True, "id": pid,
                                   "perevod": nedelnyy_perevod(con)})
            if u.path == "/api/perevod/udalit":
                if not baza.udalit_perevod(con, int(d["id"])):
                    return self._json({"ok": False, "err": "нет такого перевода"}, 404)
                return self._json({"ok": True, "perevod": nedelnyy_perevod(con)})
            if u.path == "/api/plan/ochistit":
                baza.ochistit_plan(con, d.get("mesyac"))
                return self._json({"ok": True})
            if u.path == "/api/plan/skopirovat":
                p = baza.skopirovat_plan(con, d["iz"], d["v"])
                return self._json({"ok": True, "plan": p})
            return self._json({"err": "нет такого действия"}, 404)
        except Exception as e:                       # noqa: BLE001
            return self._json({"ok": False, "err": "%s: %s" % (type(e).__name__, e)}, 500)
        finally:
            con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 — пустить телефоны домашней сети")
    ap.add_argument("--otkryt", action="store_true", help="открыть браузер при старте")
    a = ap.parse_args()

    kop = baza.rezervnaya_kopiya()
    print("Кабинет семейного учёта")
    print("  база:   %s" % baza.DB)
    if kop:
        print("  копия:  %s" % kop)
    print("  адрес:  http://%s:%d" % ("localhost" if a.host == "127.0.0.1" else a.host, a.port))
    if a.host == "0.0.0.0":
        print("  телефоны в домашней сети: http://<адрес этого ПК>:%d" % a.port)
    print("  остановить: Ctrl+C")
    if a.otkryt:
        threading.Timer(0.7, lambda: webbrowser.open("http://localhost:%d/" % a.port)).start()
    # Очередь ожидающих подключений по умолчанию 5. Когда машина занята
    # (сборки, браузерные аудиты), этого мало: соединения отбиваются, и снаружи
    # это выглядит как «кабинет не отвечает». 64 стоит дёшево.
    class Server(ThreadingHTTPServer):
        request_queue_size = 64
        daemon_threads = True

    Server((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
