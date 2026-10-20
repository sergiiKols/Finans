"""ПРОВЕРКА — прогон инвариантов расчёта. Ничего не меняет, только считает.

ГЛАВНОЕ ПРАВИЛО: всё, с чем сравниваем, берётся из ВХОДНОГО JSON, а не у кода,
который проверяем. Первая версия этого прогона спрашивала «сколько пришло»
у самого расчёта — и потому пропускала любую согласованную потерю: съешь
половину входящего баланса вместе с числом «сколько его было», и обе части
тождества уменьшатся одинаково. Приёмка внесла 10 потерь, старый прогон поймал 2.

 Исправлено 20.10.2026: проверки 6 и 8 ждали «внешний приток × доля» и ругались на то, что
 ядро делает по решениям (внутренний оборот — выручка продавца, Р-1; доход на капитал уходит в
 фонд инвестиций; минимум взноса, Р-14; погашение долга). Ожидания приведены к правилам.
 Осталось одно настоящее: «деньги» при коммуналке из накопления и нехватке фондов.
 Добавлено 25.08.2026: пропущенные проверки — доля управления, распределение
 дохода на паи, рублевый норматив накопления (P2-2). Число намеренных
 поломок в самотесте: 31.

Инварианты (все — против входных данных):
  1. ДЕНЬГИ. было = вошедшие паи + вошедшие фонды + вошедшие карманы + выручка
     + доход на капитал;  стало = личные + паи + фонды + карманы + ушедшее наружу.
     Должно совпасть до копейки.
  2. ПРИТОК. То, что расчёт считает притоком, равно выручке из JSON плюс доход
     на капитал, посчитанный по реестру активов.
  3. ВХОДЯЩИЕ ОСТАТКИ. Паи, фонды и карманы, которые увидел расчёт, равны
     записанным в балансе.
  4. ЗАТРАТЫ. Отнесённое участникам равно общим плюс личным плюс отчислениям
     в карманы из нормативов.
  5. ДВИЖЕНИЕ ФОНДОВ. вход + приход − расход = остаток по каждому фонду.
  6. ПЕРЕНОС. Баланс на следующий месяц равен состоянию расчёта на конец —
     та же сверка, что делает perenos.py.
  7. НЕТ ПАДЕНИЙ. SystemExit законен (внятный отказ), любое другое — дефект.

Запуск:
    python3 proverka.py                  # сценарии + фаззинг + цепочки
    python3 proverka.py --samoproverka   # доказать, что прогон вообще ловит
    python3 proverka.py --tiho --sluchainyh 20000 --zerno 777
Код возврата: 0 — чисто, 1 — есть нарушения.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import random
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DOPUSK = 0.01
IMENA = ["папа", "мама", "дочь", "сын", "бабушка"]
# Ключи — те же, что объявлены пользователю в server.py PRAVILA_ZATRAT.
PRAVILA = ["po_dohodu_vzroslyh", "porovnu_krome_detey", "po_dole_vyruchki_krome_detey",
           "porovnu", "po_dole_vyruchki"]


def zagruzit_yadro(put=None):
    put = put or os.path.join(HERE, "raschet.py")
    s = importlib.util.spec_from_file_location("raschet_pod_proverkoy", put)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


R = zagruzit_yadro()


def bazovye():
    return json.load(open(os.path.join(HERE, "parametry.json"), encoding="utf-8"))


def _file(put, gde):
    if isinstance(put, dict):
        return put
    p = os.path.join(gde, put)
    return json.load(open(p, encoding="utf-8")) if put and os.path.exists(p) else {}


# ───────────────────── что было на входе: только из JSON ─────────────────────

def vhodnye(par, mes, gde):
    """Всё, с чем сравниваем. Ни одного числа не спрашиваем у расчёта."""
    bal = _file(mes.get("balans"), gde) if mes.get("balans") else {}
    karm_bal = bal.get("karmany_ostatki") or {}
    vh_karm = sum(karm_bal.values())
    # Старый канал входящих карманов: ядро игнорирует те, что уже пришли балансом.
    for k, v in (mes.get("karmany_vhodyashchie") or {}).items():
        if k not in karm_bal:
            vh_karm += v

    akt = _file(mes.get("aktivy", "aktivy.json"), gde)
    kap = 0.0
    if par.get("dohod_na_kapital", {}).get("vklyuchen"):
        kap = sum(a.get("summa", 0.0) * a.get("stavka_godovyh", 0.0) / 12.0
                  for a in (akt.get("aktivy") or []))

    otchisl = sum((mes.get("karmany", par.get("karmany", {})) or {}).values())
    obshch = dict(mes.get("obshchie_zatraty") or {})
    komm = obshch.pop("коммунальные", 0.0)
    v_nakoplenii = par.get("kommunalka_v_nakoplenii", False)

    return {
        "vh_pai": sum((bal.get("imushchestvennye_pai") or {}).values()),
        "vh_fond": sum((bal.get("fondy_ostatki") or {}).values()),
        "vh_karm": vh_karm,
        "vyruchka": sum((mes.get("vneshnyaya_vyruchka") or {}).values()),
        "kapital": kap,
        # Ушедшее наружу: общие и личные затраты плюс покупки из карманов.
        # Отчисление в карман наружу НЕ уходит — оно копится в кармане.
        "naruzhu": (sum(obshch.values()) + komm
                    + sum((mes.get("lichnye_zatraty") or {}).values())
                    + sum(p.get("summa", 0.0) for p in (mes.get("pokupki_iz_karmanov") or []))),
        # Отнесённые участникам затраты: общие + личные + отчисления в карманы.
        # Когда коммуналка платится из накопления, сколько именно покрыл резерв,
        # из входных данных не узнать: это зависит от остатка фонда. Тогда точную
        # сумму затрат не проверяем — None означает «нечем сверить».
        "zatraty_uchastnikam": (None if v_nakoplenii else
                                (sum(obshch.values()) + komm
                                 + sum((mes.get("lichnye_zatraty") or {}).values())
                                 + otchisl)),
    }


# ─────────── правила дележа, пересчитанные независимо от ядра ───────────
#
# Инварианты выше стерегут СУММУ денег. Их недостаточно: любая ошибка, которая
# перекладывает деньги между людьми или между статьями, сумму сохраняет.
# Приёмка внесла 14 поломок такого рода — прогон поймал 4. Ниже — второй класс:
# проверки ДЕЛЕЖА. Правила берутся из parametry.json (это входные данные),
# и считаются здесь заново, другой формой записи, а не вызовом ядра.

def norma_iz_stupeney(par, vneshniy_dohod, pm):
    """Норма накопления по ступеням. Пересчёт независимый: ядро идёт по ступеням
    с ранним выходом, здесь — сумма по всем ступеням с обрезкой. Если ядро
    исказит норму, эти два счёта разойдутся."""
    if vneshniy_dohod <= 0:
        return 0.0
    nn = par["norma_nakopleniya"]
    k = pm if (nn.get("stupeni_v_kratnostyah_minimuma") and pm) else 1.0
    nakop = 0.0
    for st in nn["stupeni"]:
        niz = st["ot"] * k
        verh = float("inf") if st["do"] is None else st["do"] * k
        chast = max(0.0, min(vneshniy_dohod, verh) - niz)
        nakop += chast * st["dolya"]
    return nakop / vneshniy_dohod


def minimum_iz_vhoda(par, mes):
    """Прожиточный минимум месяца — из входных данных."""
    cfg = par.get("prozhitochnyy_minimum", {})
    karm = sum((mes.get("karmany", par.get("karmany", {})) or {}).values())
    if cfg.get("rezhim") == "iz_mesyaca":
        return sum((mes.get("obshchie_zatraty") or {}).values()) + karm
    return cfg.get("summa", 0.0)


def pravilo_delezha(par, mes):
    """Что можно проверить о дележе общих затрат, исходя из ОБЪЯВЛЕННОГО правила.

    Источник — описания правил, которые программа показывает человеку
    (server.py, PRAVILA_ZATRAT), а не реализация в ядре.

    Возвращает (доли, не_платят). `доли = None` значит: базу этого правила
    из входных данных не восстановить, проверяем только, кто платить НЕ должен.
    Так честнее, чем повторить формулу ядра и проверить его самим собой.
    """
    uch = list(mes.get("uchastniki") or [])
    deti = set(mes.get("deti") or [])
    otd = set(mes.get("otdelno") or [])
    vyr = mes.get("vneshnyaya_vyruchka") or {}
    est_oborot = bool(mes.get("vnutrennie_pokupki"))
    pr = par.get("obshchie_zatraty_delyatsya")

    # ВНИМАНИЕ. Освобождение живущих отдельно объявлено ТОЛЬКО для правила
    # «по доходу взрослых» («дети и живущие отдельно не платят»). Остальные
    # четыре правила про отделившихся не говорят ничего, и расчёт их не
    # освобождает. Обобщать это на все правила нельзя — я так уже ошибся.
    # Открытый вопрос владельцу, а не дефект кода.
    if pr == "porovnu":                       # «поровну между всеми, включая детей»
        return ({k: 1.0 / len(uch) for k in uch} if uch else None), set()

    if pr == "porovnu_krome_detey":           # «поровну между взрослыми»
        # В правилах «кроме детей» под детьми понимаются И живущие отдельно:
        # общие затраты дома несут те, кто с семьёй живёт (Р-4). В правиле
        # «поровну между всеми» такого освобождения нет.
        ne = set(deti) | otd
        gr = [k for k in uch if k not in ne]
        return ({k: 1.0 / len(gr) for k in gr} if gr else None), ne

    if pr in ("po_dole_vyruchki_krome_detey", "po_dole_vyruchki"):
        krome = pr.endswith("krome_detey")
        ne = (set(deti) | otd) if krome else set()
        gr = [k for k in uch if k not in ne]
        # Внутренний оборот добавляет выручку по ходу расчёта — из входных
        # данных базу уже не определить. Не проверяем то, чего не знаем.
        if est_oborot or not gr:
            return None, ne
        # Отрицательный вес — не доля: у участника с отрицательной выручкой
        # не может быть отрицательной доли затрат. То же правило, что в ядре.
        chistye = {k: max(0.0, vyr.get(k, 0.0)) for k in gr}
        baza = sum(chistye.values())
        if baza <= 1e-12:
            return None, ne
        return {k: chistye[k] / baza for k in gr}, ne

    if pr == "po_dohodu_vzroslyh":
        # Объявлено: «кто больше может — тот больше платит; дети и живущие
        # отдельно не платят». База «сколько может» складывается по ходу расчёта
        # (потребление плюс дивиденды и доля управления) — из входных данных её
        # не восстановить. Проверяем твёрдую часть обещания.
        return None, deti | otd

    return None, set()


def narusheniya_delezha(u, par, mes, gde, R_):
    """Второй класс: правильно ли поделено. Всё сравнивается с правилами из JSON."""
    ploho = []
    v = vhodnye(par, mes, gde)

    # 1. Норма накопления — против ступеней из JSON.
    pm = minimum_iz_vhoda(par, mes)
    ozhid = norma_iz_stupeney(par, v["vyruchka"], pm)
    if abs(u.norma - ozhid) > 1e-9:
        ploho.append(("норма накопления", f"расчёт применил {u.norma:.6f}, "
                                          f"по ступеням из JSON {ozhid:.6f}"))
    if abs(u.prozh_minimum - pm) > DOPUSK:
        ploho.append(("прожиточный минимум", f"расчёт {u.prozh_minimum:.2f}, из JSON {pm:.2f}"))

    # 2. Пропорции субсчетов накопления — против нормативов из JSON.
    norm = par["nakoplenie"]
    baza_n = sum(norm.values())
    ot_vyruchki = {f: u.fondy.prihod[f] - u.dohod_fondam[f] for f in norm}
    vsego_ot_vyruchki = sum(ot_vyruchki.values()) - getattr(u, "mezhdu_fondami", 0.0)
    # Погашение долга кладёт деньги в ОДИН фонд (резервный), а не пропорционально.
    # Отнести это к конкретному фонду из входных данных нельзя, поэтому в такие
    # месяцы пропорции не проверяем — честнее, чем повторять логику ядра.
    if baza_n > 1e-12 and vsego_ot_vyruchki > DOPUSK and getattr(u, "mezhdu_fondami", 0.0) <= 1e-9:
        for f, d in norm.items():
            dolzhno = vsego_ot_vyruchki * d / baza_n
            if abs(ot_vyruchki[f] - dolzhno) > max(DOPUSK, abs(dolzhno) * 1e-6):
                ploho.append(("доли субсчетов",
                              f"«{f}» получил {ot_vyruchki[f]:.2f}, по нормативу {dolzhno:.2f}"))

    # 3. Общие затраты — по объявленному правилу.
    if par.get("kommunalka_v_nakoplenii"):
        doli, ne_platyat = None, set()     # база затрат неизвестна, см. выше
    else:
        doli, ne_platyat = pravilo_delezha(par, mes)
    lichnye = mes.get("lichnye_zatraty") or {}
    pr_imya = par.get("obshchie_zatraty_delyatsya")
    for kto in ne_platyat:
        if kto in u.uchastniki and abs(u.zatraty[kto] - lichnye.get(kto, 0.0)) > DOPUSK:
            ploho.append(("платит тот, кто не должен",
                          f"«{kto}» отнесено {u.zatraty[kto]:.2f} при правиле «{pr_imya}», "
                          f"а он платить не должен"))
            break
    if doli is not None:
        otchisl = sum((mes.get("karmany", par.get("karmany", {})) or {}).values())
        obshch = dict(mes.get("obshchie_zatraty") or {})
        komm = obshch.pop("коммунальные", 0.0)
        obshchaya = sum(obshch.values()) + otchisl \
                    + (0.0 if par.get("kommunalka_v_nakoplenii") else komm)
        for kto in u.uchastniki:
            dolzhno = obshchaya * doli.get(kto, 0.0) + lichnye.get(kto, 0.0)
            if abs(u.zatraty[kto] - dolzhno) > max(DOPUSK, abs(dolzhno) * 1e-6):
                ploho.append(("дележ общих затрат",
                              f"«{kto}» отнесено {u.zatraty[kto]:.2f}, по правилу "
                              f"«{pr_imya}» {dolzhno:.2f}"))
                break

    # 4. Земельные дивиденды — поровну по праву членства.
    #    Санкция может срезать, но исходные доли обязаны быть равными.
    # Санкция не только срезает у одних, но и раздаёт срезанное другим — после неё
    # исходные доли из результата не восстановить. Проверяем месяцы без санкции.
    do_sankcii = {k: u.dividendy[k] for k in u.uchastniki}
    if u.uchastniki and not any(abs(v) > 1e-9 for v in u.sankciya.values()):
        obraz = do_sankcii[u.uchastniki[0]]
        for kto in u.uchastniki[1:]:
            if abs(do_sankcii[kto] - obraz) > DOPUSK:
                ploho.append(("дивиденды",
                              f"до санкции «{kto}» {do_sankcii[kto]:.2f}, "
                              f"«{u.uchastniki[0]}» {obraz:.2f} — должны быть поровну"))
                break

    # 5. Ничто из этого не может быть отрицательным.
    for imya, slovar in (("дивиденды", u.dividendy), ("санкция", u.sankciya),
                         ("доля управления", u.upravlenie), ("доход на пай", u.dohod_na_pai)):
        for kto, val in slovar.items():
            if val < -DOPUSK:
                ploho.append((f"{imya} отрицательные", f"«{kto}»: {val:.2f}"))
                break
    for f, val in u.fondy.items():
        if val < -DOPUSK:
            ploho.append(("фонд в минусе", f"«{f}»: {val:.2f}"))
            break

    est_oborot = bool(mes.get("vnutrennie_pokupki"))
    kap = par.get("dohod_na_kapital", {})

    # 6. ДОЛЯ УПРАВЛЕНИЯ — отчисление в фонд управления из нормы накопления.
    #    raschet.py step 4: fondy["upravlenie"] += vyruchka * nak_doli["upravlenie"].
    #    nak_doli["upravlenie"] = par["nakoplenie"]["upravlenie"] * (norma / polnaya).
    #    Организатору: upravlenie[org] = fondy["upravlenie"] (если org в составе).
    #
    #    ПРЕЖНЯЯ ОШИБКА: proverka использовала dolya_upr=0.05 из секции kapital,
    #    но это другая ставка. nak_doli["upravlenie"] может быть 0.0223 и меньше.
    #    Также база была vyruchka_total (внутренние операции раздувают), а не vneshniy_pritok.
    #
    #    Проблема: при наличии ВХОДЯЩЕГО остатка фонда управления (из баланса предыдущих
    #    месяцев) фактическое управление >> ожидаемого (баланс 1383 ₽ + 52 ₽ в месяц ≠ 52 ₽).
    #    Это НЕ баг raschet. Пропускаем если баланс задан: накопленные остатки не предсказать.
    par_nak = par.get("nakoplenie", {})
    polnaya = par.get("norma_nakopleniya", {}).get("polnaya_norma", 1.0)
    k = u.norma / polnaya if polnaya != 0.0 else 0.0
    dolya_nak_upr = par_nak.get("upravlenie", 0.0) * k
    org = par.get("poluchatel_upravleniya")
    if org and org in u.uchastniki:
        bal = mes.get("balans") or {}
        fondy_bal = (bal.get("fondy_ostatki") or {}).get("upravlenie", 0.0)
        # Пропускаем: при входящем остатке (накопилось за предыдущие месяцы),
        # при коммуналке из резерва (фонд управления может частично идти на покрытие),
        # и когда prihod <= tek_balance (баланс накоплен, а не заработан в этом месяце).
        iz_upr = any(p_.get("iz_fonda") == "upravlenie" for p_ in (mes.get("vnutrennie_pokupki") or []))
        if (abs(fondy_bal) < DOPUSK and not par.get("kommunalka_v_nakoplenii") and not iz_upr
                and u.fondy.prihod.get("upravlenie", 0) > u.fondy["upravlenie"] + DOPUSK):
            # Доход на капитал: его доля управления (kap.dolya_upravleniya) тоже идёт организатору.
            upr_kap6 = kap.get("dolya_upravleniya", 0.05)
            ozhid_upr = ((u.vneshniy_pritok - u.kapital_dohod) * dolya_nak_upr
                         + u.kapital_dohod * upr_kap6)
            fakt_upr = sum(u.upravlenie.values())
            porog = max(DOPUSK, ozhid_upr * 0.10)
            # Внутренний оборот (покупки, выплаты из фондов) — тоже выручка продавца, и доля
            # управления берётся с неё снова (Р-1). Размер оборота из входа не предсказать
            # (покупки долями), поэтому с оборотом проверяем только нижнюю границу.
            if est_oborot:
                otklonenie = max(0.0, ozhid_upr - fakt_upr)
            else:
                otklonenie = abs(fakt_upr - ozhid_upr)
            if otklonenie > porog:
                ploho.append(("доля управления",
                              f"все участники получили {fakt_upr:.2f}, "
                              f"по формуле vneshniy_pritok {u.vneshniy_pritok:.2f} × "
                              f"nak_doli(upravlenie) {dolya_nak_upr:.4f} = {ozhid_upr:.2f}"))

    # 7. ДОХОД НА ПАЙ — распределение пропорционально вложенному паю.
    #    raschet: доход на паи = ostatok * pai_vhod / (pai_vhod + fondy_vhod).
    #    proverka (старая формула): ostatok * pai_vhod / sum(pai_vhod).
    #    Если investicii > 0, raschet включает инвест.фонд в базу → доля меньше.
    #    Если vnutrennie_pokupki — внутренние операции меняют базу → пропускаем.
    #    Пропускаем также при kapital_dohod > 0: база расходится между raschet и proverka.
    if u.kapital_dohod > DOPUSK and u.pai_vhod and not est_oborot:
        # База raschet: pai_vhod + fondy_vhod. proverka видит только fondy_vhod.investicii.
        # Если в балансе есть другие фонды (кроме investicii) — база расходится.
        # Пропускаем при наличии баланса ( raschet использует все фонды, proverka — только investicii).
        bal = mes.get("balans") or {}
        fondy_vh = bal.get("fondy_ostatki") or {}
        if fondy_vh and any(f != "investicii" for f in fondy_vh):
            pass  # пропускаем — не можем вычислить правильную базу
        elif fondy_vh.get("investicii", 0.0) > DOPUSK or not fondy_vh:
            # Нет фондов вообще — пропускаем (нет данных).
            # Есть только investicii — используем их.
            fondy_vh_invest = fondy_vh.get("investicii", 0.0)
            total_invest = sum(v for v in u.pai_vhod.values() if v > 0) + fondy_vh_invest
            if total_invest >= DOPUSK:
                upr = kap.get("dolya_upravleniya", 0.05) if org and org in u.uchastniki else 0.0
                ostatok = u.kapital_dohod * (1 - upr)
                for kto, v in u.pai_vhod.items():
                    if v > 0:
                        dolzhno = ostatok * v / total_invest
                        if abs(u.dohod_na_pai.get(kto, 0.0) - dolzhno) > max(DOPUSK, dolzhno * 1e-4):
                            ploho.append(("доход на пай",
                                          f"«{kto}» ({v:.2f}) получил {u.dohod_na_pai.get(kto, 0.0):.2f}, "
                                          f"по паю+фонд должно быть {dolzhno:.2f}"))
                            break

    # 8. НОРМАТИВ НАКОПЛЕНИЯ В РУБЛЯХ — не только доли, но и сумма.
    #    Раньше проверялись только пропорции субсчетов (доли от vsego_ot_vyruchki).
    #    Если норматив читается вдвое большим — доли сойдутся, но сумма уплывёт.
    #    Проверяем: expected = (vneshniy_pritok + kapital_dohod) * norma.
    #    Но: raschet добавляет доход на капитал в prihod через фонды (mezhdu_fondami),
    #    что требует знания ВХОДЯЩИХ остатков всех фондов. proverka их не видит.
    #    При наличии баланса — пропускаем (не можем вычислить ожидаемое).
    #    При komm_v_nakoplenii=True или est_oborot=True — пропускаем (связанные эффекты).
    #    При vneshniy_pritok <= 0 — пропускаем (база отрицательная, формула не работает).
    if (par.get("kommunalka_v_nakoplenii") or est_oborot
            or u.vneshniy_pritok <= DOPUSK
            or any(x < 0 for x in mes.get("vneshnyaya_vyruchka", {}).values())  # нетто или брутто — решение не принято
            or mes.get("balans")):
        pass  # пропускаем
    else:
        # Доход на капитал не делится по нормативу: за вычетом доли управления он весь
        # уходит в фонд инвестиций (raschet.py:519–526). Остальная выручка — по норме.
        upr_kap = kap.get("dolya_upravleniya", 0.05) if org and org in u.uchastniki else 0.0
        ozhid_nak = ((u.vneshniy_pritok - u.kapital_dohod) * u.norma
                     + u.kapital_dohod * (1 - upr_kap))
        # Перекладывание между фондами и погашение долга — приход, который не из выручки.
        sum_prihod = sum(u.fondy.prihod.values()) - u.mezhdu_fondami
        # Минимум взноса в накопление (Р-14): доплата сверх нормы ложится в фонды.
        verh = ozhid_nak + sum(u.min_doplata.values())
        if not (ozhid_nak - max(DOPUSK, ozhid_nak * 1e-4) <= sum_prihod <= verh + max(DOPUSK, verh * 1e-4)):
            ploho.append(("рублёвый норматив накопления",
                          f"суммарный приход в фонды {sum_prihod:.2f}, "
                          f"по формуле vneshniy_pritok {u.vneshniy_pritok:.2f} × norma "
                          f"{u.norma:.4f} = {ozhid_nak:.2f}"))

    # 7. РАЗНОС ИНВЕСТИЦИОННОГО ФОНДА В ПАИ — главное деление системы Чартаева.
    #    Приёмка внесла поломку «разнести поровну вместо по выручке»: она изменила
    #    числа в 416 месяцах из 683, и прогон не поймал НИ ОДНОГО. Ребёнок без
    #    выручки получал 293.35 в собственность, и это считалось нормой.
    prirost = {k: u.imushch_pai[k] - u.pai_vhod[k] for k in u.uchastniki}
    for kto, v in prirost.items():
        if v < -DOPUSK:
            ploho.append(("прирост пая отрицательный", f"«{kto}»: {v:.2f}"))
            break
    vsego_prirost = sum(prirost.values())
    vyr_ras = {k: u.vyruchka[k] for k in u.uchastniki}
    baza_v = sum(v for v in vyr_ras.values() if v > 0)
    if vsego_prirost > DOPUSK and baza_v > DOPUSK:
        # Правило: капитализация идёт пропорционально выручке участника.
        # Само правило — из описания системы, доли считаем здесь, а не у ядра.
        for kto in u.uchastniki:
            dolzhno = vsego_prirost * max(0.0, vyr_ras[kto]) / baza_v
            if abs(prirost[kto] - dolzhno) > max(DOPUSK, abs(dolzhno) * 1e-6):
                ploho.append(("разнос в паи",
                              f"«{kto}» получил {prirost[kto]:.2f}, "
                              f"по доле выручки должно быть {dolzhno:.2f}"))
                break

    # 8. МАШИНА ДЕФИЦИТА — СРАБОТАЛА ЛИ ОНА, а не отчиталась ли.
    #
    #    Прошлая версия проверяла согласованность отчёта с самим собой, и её
    #    обходили в одну строку: оставить учёт честным и просто не трогать фонды.
    #    300 месяцев из 1126 менялись, ловилось 0 — семья сидела с полным минусом
    #    при полных страховом и резервном. Здесь проверяется ДЕЙСТВИЕ.
    cfg_d = par.get("deficit", {}) or {}
    if cfg_d.get("vklyuchen"):          # выключенная машина — законная настройка
        # Порог тот же, что у ядра. С DOPUSK=0.01 микродефицит в полкопейки
        # законно поднимал счётчик, а проверка звала это нарушением.
        EPS = 1e-9
        poryadok = cfg_d.get("poryadok") or []
        vozvratnye = set(cfg_d.get("vozvratnye") or [])
        pr_strah = cfg_d.get("predel_strahovogo_v_mesyac", 1.0)

        minus_do = {k: -(R_._lichnyy(u, k) - u.pokrytie_komu[k]) for k in u.uchastniki}
        # Порог тот же, что у ядра (1e-9), а не копеечный: иначе минусы меньше
        # копейки считает только ядро, и суммы расходятся на их количество.
        minus_do = {k: v for k, v in minus_do.items() if v > EPS}
        vsego_minus_do = sum(minus_do.values())

        # 8.1 Минус ОБЪЯВЛЕН: дефицит равен сумме минусов до покрытия.
        if abs(u.deficit - vsego_minus_do) > max(DOPUSK, vsego_minus_do * 1e-6):
            ploho.append(("дефицит не объявлен",
                          f"минусов до покрытия {vsego_minus_do:.2f}, "
                          f"дефицитом объявлено {u.deficit:.2f}"))

        # 8.2 Учёт покрытия сходится.
        vzyato_vsego = sum(u.pokryto.values())
        if u.deficit > EPS and abs(u.deficit - (vzyato_vsego + u.nepokryto)) > DOPUSK:
            ploho.append(("учёт покрытия",
                          f"дефицит {u.deficit:.2f} = взято {vzyato_vsego:.2f} "
                          f"+ непокрыто {u.nepokryto:.2f}?"))

        # 8.3 ГЛАВНОЕ: фонды каскада ОБЯЗАНЫ быть использованы, если что-то
        #     осталось непокрытым. Именно эту проверку обходили, оставляя
        #     отчётность честной и не трогая деньги.
        if u.nepokryto > EPS:
            for fond in poryadok:
                vzyato = u.pokryto.get(fond, 0.0)
                ostalos = max(0.0, u.fondy.get(fond, 0.0))
                bylo = vzyato + ostalos
                mozhno = bylo * (pr_strah if fond == "strahovoy" else 1.0)
                if vzyato < mozhno - DOPUSK:
                    ploho.append(("фонд не использован",
                                  f"непокрыто {u.nepokryto:.2f}, а из «{fond}» взято "
                                  f"{vzyato:.2f} при доступных {mozhno:.2f} "
                                  f"(в фонде было {bylo:.2f})"))
                    break

        # 8.4 Порядок каскада: следующий трогают, когда предыдущий исчерпан
        #     в пределах своего месячного лимита.
        for nomer, fond in enumerate(poryadok[1:], start=1):
            if u.pokryto.get(fond, 0.0) > DOPUSK:
                for ranshe in poryadok[:nomer]:
                    vz = u.pokryto.get(ranshe, 0.0)
                    ost = max(0.0, u.fondy.get(ranshe, 0.0))
                    mozhno = (vz + ost) * (pr_strah if ranshe == "strahovoy" else 1.0)
                    if vz < mozhno - DOPUSK:
                        ploho.append(("порядок покрытия",
                                      f"взято из «{fond}», хотя в «{ranshe}» можно было "
                                      f"взять ещё {mozhno - vz:.2f}"))
                        break

        # 8.5 Покрытие раздаётся ПРОПОРЦИОНАЛЬНО глубине минуса, а не как попало.
        #     Прежняя проверка была про сумму: деньги могли переехать между
        #     людьми, и она не дрожала.
        if vzyato_vsego > DOPUSK and vsego_minus_do > DOPUSK:
            for kto, m in minus_do.items():
                dolzhno = vzyato_vsego * m / vsego_minus_do
                if abs(u.pokrytie_komu[kto] - dolzhno) > max(DOPUSK, dolzhno * 1e-6):
                    ploho.append(("покрытие не по глубине минуса",
                                  f"«{kto}» получил {u.pokrytie_komu[kto]:.2f}, "
                                  f"по глубине минуса {dolzhno:.2f}"))
                    break

        # 8.6 Заём из возвратных фондов ОБЯЗАН быть записан в долг.
        zayom = sum(u.pokryto.get(f, 0.0) for f in vozvratnye)
        ozhid_dolg = u.dolg_vhod + zayom - u.pogasheno
        if abs(u.dolg - ozhid_dolg) > DOPUSK:
            ploho.append(("заём не в долге",
                          f"долг {u.dolg:.2f}, ожидалось {ozhid_dolg:.2f} "
                          f"(вход {u.dolg_vhod:.2f} + заём {zayom:.2f} "
                          f"− погашено {u.pogasheno:.2f})"))
        # И наоборот: невозвратный фонд долга НЕ создаёт.
        nevozvr = sum(u.pokryto.get(f, 0.0) for f in poryadok if f not in vozvratnye)
        if nevozvr > DOPUSK and u.dolg > u.dolg_vhod + zayom + DOPUSK:
            ploho.append(("долг из невозвратного",
                          f"из невозвратных фондов взято {nevozvr:.2f}, "
                          f"а долг вырос сверх заёма"))

        # 8.7 Счётчик месяцев подряд.
        bal_d = _file(mes.get("balans"), gde) if mes.get("balans") else {}
        ozhid_schet = (bal_d.get("mesyacev_deficita_podryad", 0) + 1
                       if u.deficit > EPS else 0)
        if u.mes_deficita != ozhid_schet:
            ploho.append(("счётчик дефицита",
                          f"расчёт показывает {u.mes_deficita}, ожидалось {ozhid_schet}"))

        # 8.8 ОСТАНОВКА как самостоятельное правило, а не следствие счётчика.
        #     Если расчёт досчитал, хотя условие остановки выполнено, — правило
        #     не сработало. Раньше это не проверялось ничем: 158 цепочек.
        predel = cfg_d.get("podryad_mesyacev_do_ostanovki")
        if u.nepokryto > EPS and predel and u.mes_deficita > predel:
            ploho.append(("остановка не сработала",
                          f"непокрыто {u.nepokryto:.2f}, месяц {u.mes_deficita}-й "
                          f"подряд при пределе {predel} — расчёт обязан был "
                          f"остановиться, но досчитал"))

    # 6. Долг и счётчик дефицита — из баланса, а не из воздуха.
    bal = _file(mes.get("balans"), gde) if mes.get("balans") else {}
    if abs(u.dolg_vhod - bal.get("dolg_rezervu", 0.0)) > DOPUSK:
        ploho.append(("входящий долг", f"расчёт видит {u.dolg_vhod:.2f}, "
                                       f"в балансе {bal.get('dolg_rezervu', 0.0):.2f}"))
    b = R_.balans_na_sleduyushchiy(u, par)
    if abs(b.get("dolg_rezervu", 0.0) - u.dolg) > DOPUSK:
        ploho.append(("перенос долга", f"в баланс {b.get('dolg_rezervu', 0.0):.2f}, "
                                       f"на конец месяца {u.dolg:.2f}"))
    # Счётчик дефицитных месяцев обязан доехать до следующего месяца. Сверять его
    # с ВХОДНЫМ балансом бессмысленно: этот баланс пишет тот же код, и враньё
    # писателя сходится с враньём читателя. Сверяем баланс с состоянием расчёта —
    # так же, как это делает perenos.py.
    if b.get("mesyacev_deficita_podryad", 0) != u.mes_deficita:
        ploho.append(("перенос счётчика дефицита",
                      f"в баланс {b.get('mesyacev_deficita_podryad', 0)}, "
                      f"на конец месяца {u.mes_deficita} — правило остановки "
                      f"на четвёртом месяце висит на этом числе"))
    return ploho


# ──────────────────────────── инварианты ────────────────────────────

def narusheniya(u, par, mes, gde, R_):
    v = vhodnye(par, mes, gde)
    ploho = []

    bylo = v["vh_pai"] + v["vh_fond"] + v["vh_karm"] + v["vyruchka"] + v["kapital"]
    stalo = (sum(R_._lichnyy(u, k) for k in u.uchastniki)
             + sum(u.imushch_pai.values()) + sum(u.fondy.values())
             + sum(u.karmany.values()) + v["naruzhu"])
    if abs(bylo - stalo) > DOPUSK:
        ploho.append(("деньги", f"было {bylo:.2f}, стало {stalo:.2f}, разрыв {bylo-stalo:+.2f}"))

    if abs(u.vneshniy_pritok - (v["vyruchka"] + v["kapital"])) > DOPUSK:
        ploho.append(("приток", f"расчёт видит {u.vneshniy_pritok:.2f}, "
                                f"в JSON {v['vyruchka'] + v['kapital']:.2f}"))
    for pole, bylo_, stalo_ in (("входящие паи", v["vh_pai"], sum(u.pai_vhod.values())),
                                ("входящие фонды", v["vh_fond"], sum(u.fondy_vhod.values())),
                                ("входящие карманы", v["vh_karm"], sum(u.karmany_vhod.values()))):
        if abs(bylo_ - stalo_) > DOPUSK:
            ploho.append((pole, f"в балансе {bylo_:.2f}, расчёт увидел {stalo_:.2f}"))
    if v["zatraty_uchastnikam"] is not None and \
            abs(sum(u.zatraty.values()) - v["zatraty_uchastnikam"]) > DOPUSK:
        ploho.append(("затраты", f"отнесено {sum(u.zatraty.values()):.2f}, "
                                 f"в JSON {v['zatraty_uchastnikam']:.2f}"))

    for f, nev in u.fondy.sverka(u.fondy_vhod):
        ploho.append(("движение фондов", f"«{f}» на {nev:+.4f}"))

    # Перенос: та же сверка, что в perenos.py — баланс против состояния расчёта.
    b = R_.balans_na_sleduyushchiy(u, par)
    for imya, iz_balansa, iz_rascheta in (
            ("паи", sum(b["imushchestvennye_pai"].values()), sum(u.imushch_pai[k] for k in u.uchastniki)),
            ("фонды", sum(b["fondy_ostatki"].values()), sum(u.fondy.values())),
            ("карманы", sum(b["karmany_ostatki"].values()), sum(u.karmany.values()))):
        if abs(iz_balansa - iz_rascheta) > DOPUSK:
            ploho.append(("перенос", f"{imya}: в баланс {iz_balansa:.2f}, "
                                     f"на конец месяца {iz_rascheta:.2f}"))
    return ploho


def poschitat(par, mes, R_):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return R_.schitat(json.loads(json.dumps(par)), json.loads(json.dumps(mes))), None, None
    except SystemExit as e:
        return None, str(e), None
    except Exception as e:  # noqa: BLE001
        return None, None, f"{type(e).__name__}: {e}"


def proverit(imya, par, mes, gde=HERE, R_=None):
    R_ = R_ or R
    u, otkaz, padenie = poschitat(par, mes, R_)
    if padenie:
        return [(imya, "ПАДЕНИЕ", padenie)]
    if otkaz is not None:
        return []
    nayd = narusheniya(u, par, mes, gde, R_) + narusheniya_delezha(u, par, mes, gde, R_)
    return [(imya, vid, txt) for vid, txt in nayd]


# ─────────────────────────── именные сценарии ───────────────────────────

def mesyats(uch, vyr, **kw):
    m = {"uchastniki": list(uch), "deti": kw.get("deti", []), "otdelno": kw.get("otdelno", []),
         "vneshnyaya_vyruchka": dict(vyr), "obshchie_zatraty": kw.get("zatraty", {"еда": 600.0}),
         "lichnye_zatraty": kw.get("lichnye", {}), "vnutrennie_pokupki": kw.get("pokupki", [])}
    for k in ("balans", "aktivy", "karmany", "karmany_vhodyashchie", "pokupki_iz_karmanov"):
        if k in kw:
            m[k] = kw[k]
    return m


def bal(pai=None, fondy=None, karmany=None, dolg=0.0, mes_def=0):
    pai, fondy, karmany = pai or {}, fondy or {}, karmany or {}
    return {"imushchestvennye_pai": pai, "fondy_ostatki": fondy, "karmany_ostatki": karmany,
            "dolg_rezervu": dolg, "mesyacev_deficita_podryad": mes_def,
            "_svodka": {"vsego": sum(pai.values()) + sum(fondy.values()) + sum(karmany.values()),
                        "pai": sum(pai.values()), "fondy": sum(fondy.values()),
                        "karmany": sum(karmany.values())}}


def imennye():
    p0 = bazovye()
    p0["karmany"] = {}
    dvoe = ["папа", "мама"]

    def par(**kw):
        p = json.loads(json.dumps(p0))
        p["poluchatel_upravleniya"] = kw.pop("org", "папа")
        p.update(kw)
        return p

    # — нулевые знаменатели —
    yield ("выручки нет ни у кого", par(obshchie_zatraty_delyatsya="po_dole_vyruchki_vseh"),
           mesyats(dvoe, {"папа": 0.0}))
    yield ("выручки нет у взрослых, зарабатывает ребёнок",
           par(obshchie_zatraty_delyatsya="po_dole_vyruchki_krome_detey"),
           mesyats(["папа", "мама", "сын"], {"сын": 900.0}, deti=["сын"]))
    yield ("в составе одни дети", par(obshchie_zatraty_delyatsya="po_dole_vyruchki_krome_detey"),
           mesyats(dvoe, {"папа": 800.0}, deti=dvoe))
    yield ("пустой состав", par(obshchie_zatraty_delyatsya="po_dole_vyruchki_vseh"), mesyats([], {}))
    yield ("отрицательная выручка", par(), mesyats(dvoe, {"папа": 4000.0, "мама": -1000.0}))
    yield ("один участник", par(), mesyats(["папа"], {"папа": 1500.0}))

    # — доли субсчетов: ноль, половина, отсутствующий субсчёт —
    for mnozh, podpis in ((0.0, "нулевые"), (0.5, "половинные"), (2.0, "удвоенные")):
        p = par()
        p["nakoplenie"] = {f: d * mnozh for f, d in p["nakoplenie"].items()}
        yield (f"доли субсчетов {podpis} при полной норме 0.5", p,
               mesyats(dvoe, {"папа": 4000.0}, zatraty={}))
    p = par()
    p["nakoplenie"] = {f: d for f, d in p["nakoplenie"].items() if f != "investicii"}
    p["nakoplenie"]["rezerv"] = p["nakoplenie"]["rezerv"] + 0.2
    yield ("субсчёта investicii нет в нормативах", p,
           mesyats(dvoe, {"папа": 1000.0}, zatraty={},
                   aktivy={"aktivy": [{"summa": 120000.0, "stavka_godovyh": 0.12}]}))

    # — капитал и получатель доли управления —
    yield ("капитал есть, входящих паёв и фондов нет", par(),
           mesyats(dvoe, {"папа": 1000.0}, zatraty={},
                   aktivy={"aktivy": [{"summa": 120000.0, "stavka_godovyh": 0.12}]}))
    yield ("капитал есть, входящие паи есть", par(),
           mesyats(dvoe, {"папа": 1000.0}, zatraty={}, balans=bal(pai={"папа": 500.0}),
                   aktivy={"aktivy": [{"summa": 120000.0, "stavka_godovyh": 0.12}]}))
    for org, podpis in ((None, "не задан"), ("", "пустая строка"), ("Лена", "вне состава")):
        yield (f"получатель доли управления {podpis}", par(org=org),
               mesyats(dvoe, {"папа": 1000.0}, zatraty={}, balans=bal(pai={"папа": 500.0}),
                       aktivy={"aktivy": [{"summa": 120000.0, "stavka_godovyh": 0.12}]}))

    # — внутренний оборот, личные затраты, карманы —
    yield ("внутренняя покупка суммой", par(),
           mesyats(dvoe, {"папа": 2000.0},
                   pokupki=[{"komu": "мама", "chto": "уборка", "ot_uchastnika": "папа", "summa": 400.0}]))
    yield ("внутренняя покупка долей потребления", par(),
           mesyats(dvoe, {"папа": 2000.0},
                   pokupki=[{"komu": "мама", "chto": "уборка", "ot_uchastnika": "папа",
                             "dolya_potrebleniya": 0.3}]))
    yield ("выплата из фонда суммой", par(),
           mesyats(dvoe, {"папа": 2000.0}, balans=bal(fondy={"obrazovanie": 500.0}),
                   pokupki=[{"komu": "мама", "chto": "занятия", "iz_fonda": "obrazovanie",
                             "summa": 300.0}]))
    yield ("выплата из фонда долей", par(),
           mesyats(dvoe, {"папа": 2000.0}, balans=bal(fondy={"obrazovanie": 500.0}),
                   pokupki=[{"komu": "мама", "chto": "занятия", "iz_fonda": "obrazovanie",
                             "dolya_fonda": 0.5}]))
    yield ("выплата из пустого фонда ЗЕМЕЛЬНЫХ ПАЁВ (печатный станок)", par(),
           mesyats(dvoe, {"папа": 0.0}, zatraty={"еда": 100.0},
                   pokupki=[{"komu": "папа", "chto": "выплата",
                             "iz_fonda": "zemelnye_pai", "summa": 100.0}]))
    yield ("выплата из фонда больше остатка", par(),
           mesyats(dvoe, {"папа": 1000.0}, balans=bal(fondy={"rezerv": 100.0}),
                   pokupki=[{"komu": "мама", "chto": "выплата",
                             "iz_fonda": "rezerv", "summa": 900.0}]))
    yield ("покупка из пустого фонда", par(),
           mesyats(dvoe, {"папа": 1000.0}, zatraty={},
                   pokupki=[{"komu": "мама", "chto": "уборка", "iz_fonda": "obrazovanie",
                             "summa": 5000.0}]))
    yield ("личные затраты участника", par(),
           mesyats(dvoe, {"папа": 2000.0}, lichnye={"мама": 250.0}))
    yield ("карман: покупка больше остатка", par(karmany={"одежда": 100.0}),
           mesyats(dvoe, {"папа": 1000.0}, zatraty={}, karmany_vhodyashchie={"одежда": 50.0},
                   pokupki_iz_karmanov=[{"karman": "одежда", "summa": 400.0}]))
    yield ("карман задан и балансом, и полем месяца", par(karmany={"одежда": 100.0}),
           mesyats(dvoe, {"папа": 1000.0}, zatraty={}, balans=bal(karmany={"одежда": 200.0}),
                   karmany_vhodyashchie={"одежда": 200.0}))

    # — баланс —
    yield ("инвестфонд есть, выручки нет", par(),
           mesyats(dvoe, {"папа": 0.0}, zatraty={}, balans=bal(fondy={"investicii": 5000.0})))
    yield ("баланс без контрольной суммы", par(),
           mesyats(dvoe, {"папа": 1000.0}, zatraty={},
                   balans={"imushchestvennye_pai": {"папа": 800.0}, "fondy_ostatki": {},
                           "karmany_ostatki": {}, "dolg_rezervu": 0.0,
                           "mesyacev_deficita_podryad": 0}))
    yield ("минимум не выполнил никто", par(),
           mesyats(dvoe, {"папа": 1000.0, "мама": 200.0}, zatraty={"еда": 4000.0},
                   balans=bal(fondy={"zemelnye_pai": 1000.0})))
    for pr in PRAVILA:
        yield (f"нулевая выручка, правило «{pr}»", par(obshchie_zatraty_delyatsya=pr),
               mesyats(["папа", "мама", "сын"], {"папа": 0.0}, deti=["сын"]))

    # P2-2: коммуналка из фонда — закрывает пробел, где пропускалась проверка долей.
    yield ("коммуналка из фонда, фонд достаточен", par(kommunalka_v_nakoplenii=True),
           mesyats(dvoe, {"папа": 3000.0}, balans=bal(fondy={"rezerv": 1000.0}),
                   zatraty={"еда": 600.0, "коммунальные": 300.0}))
    yield ("коммуналка из фонда, фонд не достаточен (частичное покрытие)", par(kommunalka_v_nakoplenii=True),
           mesyats(dvoe, {"папа": 2000.0}, balans=bal(fondy={"rezerv": 100.0}),
                   zatraty={"еда": 600.0, "коммунальные": 500.0}))


# ─────────────────────────── фаззинг ───────────────────────────

def sluchainyy(rnd, p0):
    p = json.loads(json.dumps(p0))
    p["obshchie_zatraty_delyatsya"] = rnd.choice(PRAVILA)
    n = rnd.randint(1, 5)
    uch = IMENA[:n]
    p["poluchatel_upravleniya"] = rnd.choice(uch + [None, "", "Лена"])
    if rnd.random() < 0.10:
        p["kommunalka_v_nakoplenii"] = True
    m_dol = rnd.choice([1.0, 1.0, 1.0, 0.0, 0.5, 2.0])
    if m_dol != 1.0:
        p["nakoplenie"] = {f: d * m_dol for f, d in p["nakoplenie"].items()}
    if rnd.random() < 0.08:
        p["nakoplenie"] = {f: d for f, d in p["nakoplenie"].items() if f != "investicii"}
    if rnd.random() < 0.15:
        p["karmany"] = {"одежда": round(rnd.uniform(0, 300), 2)}

    deti = [k for k in uch if rnd.random() < 0.35]
    otd = [k for k in uch if rnd.random() < 0.12]
    r = rnd.random()
    if r < 0.20:
        vyr = {k: 0.0 for k in uch}
    elif r < 0.24:
        vyr = {k: round(rnd.uniform(-1500, 0), 2) for k in uch}
    elif r < 0.34:
        # СМЕШАННЫЙ ЗНАК — самый опасный случай и его-то в генераторе не было.
        # Отрицательный вес при положительном фонде уводил чужой пай в минус,
        # а «все отрицательные» этого не вскрывают: там разносить вообще нечего.
        vyr = {k: round(rnd.uniform(-1200, 3000), 2) for k in uch}
        if all(v <= 0 for v in vyr.values()):
            vyr[uch[0]] = round(rnd.uniform(500, 3000), 2)
    else:
        vyr = {k: round(rnd.uniform(0, 3000), 2) for k in uch if rnd.random() < 0.8} \
              or {uch[0]: 1000.0}

    zat = {"еда": round(rnd.uniform(0, 3000), 2)}
    if rnd.random() < 0.4:
        zat["коммунальные"] = round(rnd.uniform(0, 600), 2)
    m = mesyats(uch, vyr, deti=deti, otdelno=otd, zatraty=zat,
                lichnye=({uch[0]: round(rnd.uniform(0, 400), 2)} if rnd.random() < 0.3 else {}))
    if rnd.random() < 0.3 and len(uch) > 1:
        if rnd.random() < 0.5:
            m["vnutrennie_pokupki"].append({"komu": uch[1], "chto": "труд",
                                            "ot_uchastnika": uch[0],
                                            "summa": round(rnd.uniform(0, 800), 2)})
        else:
            m["vnutrennie_pokupki"].append({"komu": uch[1], "chto": "труд",
                                            "ot_uchastnika": uch[0],
                                            "dolya_potrebleniya": round(rnd.uniform(0, 0.6), 2)})
    # Выплаты ИЗ ФОНДОВ. Их не было в первом фаззере вовсе — и именно через них
    # приёмка нашла печатный станок: выплата из пустого фонда земельных паёв.
    if rnd.random() < 0.35:
        fond = rnd.choice(list(bazovye()["nakoplenie"]))
        if rnd.random() < 0.5:
            m["vnutrennie_pokupki"].append({"komu": rnd.choice(uch), "chto": "выплата",
                                            "iz_fonda": fond,
                                            "summa": round(rnd.uniform(0, 1200), 2)})
        else:
            m["vnutrennie_pokupki"].append({"komu": rnd.choice(uch), "chto": "выплата",
                                            "iz_fonda": fond,
                                            "dolya_fonda": round(rnd.uniform(0, 1.5), 2)})
    if rnd.random() < 0.2 and p.get("karmany"):
        k = list(p["karmany"])[0]
        m["pokupki_iz_karmanov"] = [{"karman": k, "summa": round(rnd.uniform(0, 900), 2)}]
    if rnd.random() < 0.5:
        m["balans"] = bal(pai={k: round(rnd.uniform(0, 1500), 2) for k in uch if rnd.random() < 0.5},
                          fondy={f: round(rnd.uniform(0, 2000), 2)
                                 for f in list(bazovye()["nakoplenie"])[:rnd.randint(0, 7)]},
                          karmany=({"одежда": round(rnd.uniform(0, 400), 2)}
                                   if rnd.random() < 0.4 else {}),
                          dolg=round(rnd.uniform(0, 300), 2), mes_def=rnd.randint(0, 3))
    if rnd.random() < 0.3:
        m["aktivy"] = {"aktivy": [{"summa": round(rnd.uniform(0, 200000), 2),
                                   "stavka_godovyh": rnd.choice([0.0, 0.03, 0.12])}]}
    return p, m


def cepochka(rnd, p0, dlina=6, R_=None):
    R_ = R_ or R
    p, m = sluchainyy(rnd, p0)
    b = m.pop("balans", None)
    for i in range(dlina):
        mm = json.loads(json.dumps(m))
        if b:
            mm["balans"] = b
        mm["vneshnyaya_vyruchka"] = {k: round(v * rnd.uniform(0, 2), 2)
                                     for k, v in m["vneshnyaya_vyruchka"].items()}
        u, otkaz, padenie = poschitat(p, mm, R_)
        if padenie:
            return [(f"цепочка, месяц {i+1}", "ПАДЕНИЕ", padenie)]
        if otkaz is not None:
            return []
        nar = narusheniya(u, p, mm, HERE, R_) + narusheniya_delezha(u, p, mm, HERE, R_)
        if nar:
            return [(f"цепочка, месяц {i+1}", v, t) for v, t in nar]
        b = R_.balans_na_sleduyushchiy(u, p)
    return []


# ─────────────────── самопроверка: ловит ли прогон вообще ───────────────────

LOMKI = [
    ("входящий пай съеден вдвое",
     '        u.pai_vhod[kto] += v\n        u.imushch_pai[kto] += v',
     '        u.pai_vhod[kto] += v*0.5\n        u.imushch_pai[kto] += v*0.5'),
    ("входящий фонд съеден вдвое",
     '        u.fondy_vhod[f] += v\n        u.fondy[f] += v',
     '        u.fondy_vhod[f] += v*0.5\n        u.fondy[f] += v*0.5'),
    ("входящий карман съеден вдвое",
     '        u.karmany_vhod[imya] += v\n        u.karmany[imya] += v',
     '        u.karmany_vhod[imya] += v*0.5\n        u.karmany[imya] += v*0.5'),
    ("внешняя выручка срезана на 10 %",
     '        u.vneshniy_pritok += summa\n        u.vyruchka_uchastnika(kto, summa, "зарплата")',
     '        u.vneshniy_pritok += summa*0.9\n        u.vyruchka_uchastnika(kto, summa*0.9, "зарплата")'),
    ("отрицательная выручка выброшена",
     '        u.vneshniy_pritok += summa\n        u.vyruchka_uchastnika(kto, summa, "зарплата")',
     '        if summa <= 0: continue\n        u.vneshniy_pritok += summa\n'
     '        u.vyruchka_uchastnika(kto, summa, "зарплата")'),
    ("внутренняя покупка доходит наполовину",
     '        self.vyruchka_uchastnika(prodavec, summa, f"внутри: {chto}")',
     '        self.vyruchka_uchastnika(prodavec, summa*0.5, f"внутри: {chto}")'),
    ("личные затраты теряются",
     '    for kto, summa in mes.get("lichnye_zatraty", {}).items():\n        u.zatraty[kto] += summa',
     '    for kto, summa in mes.get("lichnye_zatraty", {}).items():\n        pass'),
    ("отчисление в карман доходит наполовину",
     '    for imya, mesyachnoe in karmany.items():\n        u.karmany[imya] += mesyachnoe',
     '    for imya, mesyachnoe in karmany.items():\n        u.karmany[imya] += mesyachnoe*0.5'),
    ("дивиденды раздаются наполовину",
     '        for kto in u.uchastniki:\n            u.dividendy[kto] += dolya',
     '        for kto in u.uchastniki:\n            u.dividendy[kto] += dolya*0.5'),
    ("перенос теряет половину карманов",
     '    karmany = {k: v for k, v in u.karmany.items()}',
     '    karmany = {k: v*0.5 for k, v in u.karmany.items()}'),
    # — ниже поломки ДЕЛЕЖА: сумма денег сохраняется, поделено неверно.
    #   Первый класс инвариантов их не видит по построению.
    ("норма накопления вдвое меньше",
     '    return 0.0 if vneshniy_dohod <= 0 else nakop / vneshniy_dohod',
     '    return 0.0 if vneshniy_dohod <= 0 else nakop / vneshniy_dohod / 2'),
    ("земельные дивиденды все первому",
     '        dolya = zem / len(u.uchastniki)\n        for kto in u.uchastniki:\n'
     '            u.dividendy[kto] += dolya',
     '        for kto in u.uchastniki[:1]:\n            u.dividendy[kto] += zem'),
    ("общие затраты делятся поровну вопреки правилу",
     '            doli, est, otric = raznesti(obshchaya_summa, {k: u.vyruchka[k] for k in vzr})',
     '            doli, est, otric = raznesti(obshchaya_summa, {k: 1.0 for k in vzr})'),
    ("входящий долг резерву забыт",
     '    u.dolg_vhod = u.dolg = bal.get("dolg_rezervu", 0.0)',
     '    u.dolg_vhod = u.dolg = 0.0'),
    ("прожиточный минимум всегда ноль",
     '    u.prozh_minimum = pm',
     '    pm = 0.0\n    u.prozh_minimum = pm'),
    ("перенос теряет долг резерву",
     '        "dolg_rezervu": u.dolg,',
     '        "dolg_rezervu": 0.0,'),
    # — поломки МАШИНЫ ДЕФИЦИТА: деньги сходятся, доли сходятся, машина не работает.
    # — поломки, которые прошлая версия инвариантов пропускала целиком.
    ("выключено только ПОКРЫТИЕ, отчётность честная",
     '        u.fondy[fond] -= vzyat\n        u.pokryto[fond] += vzyat',
     '        u.pokryto[fond] += vzyat'),
    ("резервный фонд не трогаем никогда",
     '    for fond in cfg.get("poryadok", []):',
     '    for fond in [f for f in cfg.get("poryadok", []) if f != "rezerv"]:'),
    ("остановка на четвёртом месяце отключена",
     '    if u.nepokryto > 1e-9 and predel and u.mes_deficita > predel:',
     '    if False:'),
    ("заём из резерва не пишется в долг",
     '            u.dolg += vzyat            # заём: вернём из накопления хорошего месяца',
     '            pass'),
    ("покрытие раздаётся поровну, а не по глубине минуса",
     '        for kto, m in minusy.items():\n            u.pokrytie_komu[kto] += vzyat * m / u.deficit',
     '        for kto, m in minusy.items():\n            u.pokrytie_komu[kto] += vzyat / len(minusy)'),
    ("счётчик дефицита не переносится в баланс",
     '        "mesyacev_deficita_podryad": u.mes_deficita,',
     '        "mesyacev_deficita_podryad": 0,'),
    ("машина дефицита выключена целиком",
     'def _deficit(u, par, mes):',
     'def _deficit(u, par, mes):\n    return'),
    ("покрытие берётся из резерва раньше страхового",
     '    for fond in cfg.get("poryadok", []):',
     '    for fond in list(reversed(cfg.get("poryadok", []))):'),
    ("счётчик дефицитных месяцев не растёт",
     '    u.mes_deficita += 1',
     '    u.mes_deficita += 0'),
    ("непокрытое не записывается",
     '    u.nepokryto = max(0.0, ostatok)',
     '    u.nepokryto = 0.0'),
    # — личная трата: ложится на того, кто потратил, и ни на кого больше.
    #   Ядро это умело всегда, кабинет проведён 20.08.2026.
    ("личная трата поднимает прожиточный минимум",
     '    obyaz = sum(mes.get("obshchie_zatraty", {}).values())',
     '    obyaz = (sum(mes.get("obshchie_zatraty", {}).values())\n'
     '             + sum(mes.get("lichnye_zatraty", {}).values()))'),
    ("личная трата размазывается на всех",
     '    for kto, summa in mes.get("lichnye_zatraty", {}).items():\n        u.zatraty[kto] += summa',
     '    for kto, summa in mes.get("lichnye_zatraty", {}).items():\n'
     '        for _k in u.uchastniki:\n            u.zatraty[_k] += summa / len(u.uchastniki)'),
    ("отрицательный вес снова допущен в разнос",
     '    chistye = {k: max(0.0, v) for k, v in vesa.items()}',
     '    chistye = {k: v for k, v in vesa.items()}'),
    ("инвестфонд разносится в паи ПОРОВНУ вместо по выручке",
     '        doli, est, otric = raznesti(inv, {k: u.vyruchka[k] for k in u.uchastniki})',
     '        doli, est, otric = raznesti(inv, {k: 1.0 for k in u.uchastniki})'),
    ("доход на капитал весь первому пайщику",
     '                for kto, v in u.pai_vhod.items():\n                    if v:\n'
     '                        u.dohod_na_pai[kto] += ostatok * v / baza',
     '                for kto in list(u.pai_vhod)[:1]:\n'
     '                    u.dohod_na_pai[kto] += ostatok'),
]


def samoproverka(n_mesyacev=300, zerno=1):
    """Внести известные потери в копию ядра и потребовать, чтобы прогон их поймал.

    Прогон, который ничего не ловит, проходит любую проверку. Поэтому он обязан
    уметь доказывать себя сам.
    """
    sys.dont_write_bytecode = True
    ishodnik = open(os.path.join(HERE, "raschet.py"), encoding="utf-8").read()
    vremenno = tempfile.mkdtemp(prefix="proverka_")
    for f in ("parametry.json", "aktivy.json", "balans.json"):
        p = os.path.join(HERE, f)
        if os.path.exists(p):
            shutil.copy(p, vremenno)
    itog = []
    try:
        for nomer, (imya, bylo, stalo) in enumerate(LOMKI):
            if bylo not in ishodnik:
                itog.append((imya, "ЯКОРЬ НЕ НАЙДЕН"))
                continue
            put = os.path.join(vremenno, f"slomannoe_{nomer}.py")
            open(put, "w", encoding="utf-8").write(ishodnik.replace(bylo, stalo))
            R_slom = zagruzit_yadro(put)
            rnd = random.Random(zerno)
            p0 = bazovye()
            poymano = 0
            for _ in range(n_mesyacev):
                par, mes = sluchainyy(rnd, p0)
                if proverit("x", par, mes, vremenno, R_slom):
                    poymano += 1
            # Цепочки обязательны: поломки ПЕРЕНОСА в одиночном месяце невидимы —
            # там входной баланс делает генератор, а не проверяемый код.
            # Именно на этом «счётчик не переносится в баланс» проходил незаметно.
            for _ in range(max(20, n_mesyacev // 6)):
                if cepochka(rnd, p0, R_=R_slom):
                    poymano += 1
            itog.append((imya, poymano))
    finally:
        shutil.rmtree(vremenno, ignore_errors=True)
    return itog


# ─────────────────────────────── запуск ───────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sluchainyh", type=int, default=6000)
    ap.add_argument("--cepochek", type=int, default=800)
    ap.add_argument("--zerno", type=int, default=20260819)
    ap.add_argument("--tiho", action="store_true")
    ap.add_argument("--samoproverka", action="store_true")
    a = ap.parse_args()

    if a.samoproverka:
        print("САМОПРОВЕРКА: вношу известные потери в копию ядра.")
        print("Прогон обязан поймать каждую. Ноль — прогон слеп на этом месте.\n")
        plohо = 0
        for imya, poymano in samoproverka():
            if poymano == "ЯКОРЬ НЕ НАЙДЕН":
                print(f"  ⚠ {imya:<42} якорь замены не найден — поломка не внесена")
                plohо += 1
            elif poymano == 0:
                print(f"  ✗ {imya:<42} НЕ ПОЙМАНО")
                plohо += 1
            else:
                print(f"  ✓ {imya:<42} поймано на {poymano} месяцах из 300")
        print("\nПРОГОН ЛОВИТ ВСЁ ЗАЯВЛЕННОЕ" if not plohо
              else f"\nПРОГОН СЛЕП НА {plohо} ПОЗИЦИЯХ")
        return 1 if plohо else 0

    vse = []
    n_im = 0
    for imya, par, mes in imennye():
        n_im += 1
        naydeno = proverit(imya, par, mes)
        vse += naydeno
        if not a.tiho:
            print(("  ✗ " if naydeno else "  ✓ ") + imya)
            for _, vid, txt in naydeno:
                print(f"        {vid}: {txt}")

    rnd = random.Random(a.zerno)
    p0 = bazovye()
    for i in range(a.sluchainyh):
        par, mes = sluchainyy(rnd, p0)
        vse += proverit(f"случайный #{i}", par, mes)
    for _ in range(a.cepochek):
        vse += cepochka(rnd, p0)

    vidy = {}
    for _, vid, _ in vse:
        vidy[vid] = vidy.get(vid, 0) + 1
    print(f"\nименных сценариев {n_im}, случайных месяцев {a.sluchainyh}, "
          f"цепочек {a.cepochek} по 6")
    if not vse:
        print("НАРУШЕНИЙ НЕТ")
        return 0
    print(f"НАРУШЕНИЙ: {len(vse)}")
    for vid, k in sorted(vidy.items(), key=lambda x: -x[1]):
        print(f"  {vid}: {k}")
    for imya, vid, txt in vse[:15]:
        print(f"    · {imya} — {vid}: {txt}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
