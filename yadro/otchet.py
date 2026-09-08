#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Подробный отчёт за месяц: каждое отчисление по каждой позиции.

Считает тем же ядром (raschet.py), печатает разбор в Markdown.
Запуск:  python3 otchet.py [mesyac.json] > ../otchet-mesyac.md
"""
import json, io, os, sys, importlib.util
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("raschet", os.path.join(HERE, "raschet.py"))
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)

IMENA = {"investicii": "Инвестиционный фонд", "zemelnye_pai": "Дивиденды на земельные паи",
         "upravlenie": "Доля управления", "zdravoohranenie": "Фонд здравоохранения",
         "obrazovanie": "Фонд образования", "rezerv": "Резервный фонд",
         "strahovoy": "Страховой фонд"}

MES_FILE = sys.argv[1] if len(sys.argv) > 1 else "mesyac.json"
par = json.load(io.open(os.path.join(HERE, "parametry.json"), encoding="utf-8"))
mes = json.load(io.open(os.path.join(HERE, MES_FILE), encoding="utf-8"))
u = R.schitat(par, mes)
fakt_summy = {}
for _op, (_ist, _kto, _sum, _n, _p) in zip(
        [o for o in mes.get("vnutrennie_pokupki", [])],
        [z for z in u.zhurnal if z[0].startswith("внутри")]):
    fakt_summy[id(_op)] = _sum
uch = u.uchastniki
deti = set(mes.get("deti", []))
nak = u.nak_doli
val = par.get("valyuta", "")


def s_cena():
    for op in mes.get("vnutrennie_pokupki", []):
        if "ведение хозяйства" in op.get("chto", ""):
            if "dolya_potrebleniya" in op:
                return f"{op['dolya_potrebleniya']*100:.0f} % фонда потребления папы"
            return f"{op['summa']:.0f}"
    return "—"


def s(x):  # деньги
    if abs(x) < 0.005:
        x = 0.0
    return f"{x:,.2f}".replace(",", " ")


print(f"# Семейный учёт по системе Чартаева — отчёт за месяц\n")
if MES_FILE != "mesyac.json":
    print(f"> **Это расчёт по сценарию**, не фактический месяц. Исходные данные: `{MES_FILE}`\n>\n> {mes['_']}\n")
print(f"Сформирован автоматически ядром `yadro/otchet.py`, параметры `{par['_versiya']}`. "
      f"Валюта — {val} Пересобрать: `python3 yadro/otchet.py`\n")
print("## 0. По каким правилам считано\n")
print("| Решение | Что принято |")
print("|---|---|")
print("| Р-1 | Внутренний семейный учёт, не юридическое лицо |")
print("| Р-2 | Зарплата — выручка участника: делится по общей формуле, как любая другая выручка |")
print("| Р-3 | Участники: " + ", ".join(uch) + "; дети — " + ", ".join(sorted(deti)) + " |")
PRAVILA_ZATRAT = {"po_dohodu_vzroslyh": "пропорционально доходу взрослых",
                  "porovnu": "поровну между взрослыми",
                  "porovnu_vsemi": "поровну между всеми участниками"}
print("| Р-4 | Общие затраты делятся %s, дети их не несут |"
      % PRAVILA_ZATRAT.get(par.get("obshchie_zatraty_delyatsya"), par.get("obshchie_zatraty_delyatsya")))
print(f"| Р-5 | Ведение хозяйства — покупаемый результат, цена {s_cena()} |")
print("| Р-6 | Коммунальные платежи — материальные затраты, а не расход фонда |")
print(f"| Р-7 | Норма накопления зависит от дохода семьи по ступеням; цены внутренних продуктов заданы долями; общие затраты делятся по доходу взрослых |")
print(f"| **Этот месяц** | внешний доход семьи {s(u.vneshniy_pritok)} → норма накопления "
      f"**{u.norma*100:.1f} %**, в потребление {100-u.norma*100:.1f} % |")
print()

# --- 1. Откуда взялась выручка
print("## 1. Поступления: кто, за что и от кого\n")
print("| Получатель | За что | Источник | Сумма |")
print("|---|---|---|---:|")
vsego_vnesh = 0.0
for kto, summa in mes.get("vneshnyaya_vyruchka", {}).items():
    print(f"| {kto} | зарплата и внешний доход | **извне семьи** | {s(summa)} |")
    vsego_vnesh += summa
vnutr = 0.0
for op in mes.get("vnutrennie_pokupki", []):
    ist = f"от участника: {op['ot_uchastnika']}" if op.get("ot_uchastnika") else f"фонд: {IMENA.get(op.get('iz_fonda'), op.get('iz_fonda'))}"
    fakt = fakt_summy.get(id(op), op.get("summa", 0.0))
    print(f"| {op['komu']} | {op['chto']} | {ist} | {s(fakt)} |")
    vnutr += fakt
print(f"| | | **извне** | **{s(vsego_vnesh)}** |")
print(f"| | | **внутренний оборот** | **{s(vnutr)}** |")
print(f"| | | **всего выручки** | **{s(vsego_vnesh + vnutr)}** |\n")
print(f"Реальных денег в семью пришло {s(vsego_vnesh)}. Остальные {s(vnutr)} — "
      f"перекладывание внутри семьи; они увеличивают выручку участников, но не богатство семьи.\n")

# --- 2. Как разделилась выручка каждого
print("## 2. Деление выручки каждого участника\n")
print(f"В накопление {u.norma*100:.1f} % выручки каждого (норма этого месяца), остальное — "
      f"в потребление. Накопление разносится по субсчетам.\n")
head = "| Куда | норматив | " + " | ".join(uch) + " | ИТОГО |"
print(head); print("|---|---:|" + "---:|" * (len(uch) + 1))
itogi = defaultdict(float)
for f, dolya in nak.items():
    row = [f"| {IMENA[f]} | {dolya*100:.0f} % "]
    for kto in uch:
        v = u.vyruchka[kto] * dolya; itogi[f] += v
        row.append(f"| {s(v)} ")
    row.append(f"| **{s(itogi[f])}** |")
    print("".join(row))
potr = {kto: u.vyruchka[kto] * (1 - u.norma) for kto in uch}
print(f"| **Фонд потребления** | {(1-u.norma)*100:.1f} % | "
      + " | ".join(s(potr[k]) for k in uch) + f" | **{s(sum(potr.values()))}** |")
print(f"| **Выручка всего** | 100 % | "
      + " | ".join(s(u.vyruchka[k]) for k in uch)
      + f" | **{s(sum(u.vyruchka.values()))}** |\n")

# --- 3. Движение фондов
print("## 3. Движение фондов: приход, расход, остаток\n")
rashody = defaultdict(list)
for op in mes.get("vnutrennie_pokupki", []):
    if op.get("iz_fonda"):
        rashody[op["iz_fonda"]].append((op["komu"], op["chto"],
                                        fakt_summy.get(id(op), op.get("summa", 0.0))))
# Приход и расход берём из самоучёта ядра, а не пересчитываем здесь заново:
# прежняя таблица не видела ни входящих остатков, ни доплаты минимума, ни
# покрытия дефицита, и её «Итого» не равнялось сумме собственных строк.
print("| Фонд | Вход | Приход | Расход | На что израсходован | Остаток |")
print("|---|---:|---:|---:|---|---:|")
vsego_vh = vsego_pr = vsego_rs = 0.0
for f in nak:
    vhod = u.fondy_vhod[f]
    prihod = u.fondy.prihod[f]
    rash = u.fondy.rashod[f]
    # Описание строим от ФАКТИЧЕСКОГО расхода. Раньше оно предполагалось по
    # названию фонда и противоречило соседней колонке: «не расходовался» при
    # расходе 400.00 и «доля управления → Лена» при расходе 0.00.
    naznach = "; ".join(f"{k} — {c} ({s(v)})" for k, c, v in rashody.get(f, []))
    if rash <= 1e-9:
        naz = "не расходовался"
    elif f == "investicii":
        naz = "разнесён в имущественные паи участников"
    elif f == "zemelnye_pai":
        naz = f"дивиденды поровну всем ({len(uch)} чел.)"
    elif f == "upravlenie":
        naz = f"доля управления → {par.get('poluchatel_upravleniya')}"
    else:
        naz = naznach or "расход без пояснения (покрытие дефицита, заём или доплата)"
    if naznach and f in ("investicii", "zemelnye_pai", "upravlenie"):
        naz += "; " + naznach
    vsego_vh += vhod; vsego_pr += prihod; vsego_rs += rash
    print(f"| {IMENA[f]} | {s(vhod)} | {s(prihod)} | {s(rash)} | {naz} | **{s(u.fondy[f])}** |")
ost = sum(u.fondy.values())
print(f"| **Итого** | **{s(vsego_vh)}** | **{s(vsego_pr)}** | **{s(vsego_rs)}** | | **{s(ost)}** |")
nevyazki = u.fondy.sverka(u.fondy_vhod)
if nevyazki:
    print("\n> ⚠ вход + приход − расход не равны остатку: "
          + "; ".join(f"{IMENA.get(f, f)} на {s(v)}" for f, v in nevyazki))
print()

# --- 4. Личный итог
print("## 4. Личный доход: из чего сложился\n")
print("| Строка | " + " | ".join(uch) + " | ИТОГО |")
print("|---|" + "---:|" * (len(uch) + 1))


def stroka(nazv, znach, zhirno=False):
    f = (lambda x: f"**{s(x)}**") if zhirno else (lambda x: s(x))
    # Итог считаем только по участникам, показанным в столбцах. Прежде брался
    # весь словарь целиком, и в ИТОГО попадали посторонние: строка показывала
    # ноль у всех, а в итоге 0.62 — доля управления человеку вне состава.
    print(f"| {'**' + nazv + '**' if zhirno else nazv} | "
          + " | ".join(f(znach[k]) for k in uch) + f" | {f(sum(znach[k] for k in uch))} |")


kupil = defaultdict(float)
for op in mes.get("vnutrennie_pokupki", []):
    if op.get("ot_uchastnika"):
        kupil[op["ot_uchastnika"]] += fakt_summy.get(id(op), op.get("summa", 0.0))
stroka(f"Фонд потребления ({(1-u.norma)*100:.1f} % выручки)", potr)
stroka("− куплено у других участников", {k: -kupil[k] for k in uch})
stroka("− материальные затраты (еда, коммуналка)", {k: -u.zatraty[k] for k in uch})
stroka("+ дивиденды на земельный пай", u.dividendy)
stroka("+ доля управления", u.upravlenie)
if u.pokrytie_komu and any(u.pokrytie_komu.values()):
    stroka("+ покрытие дефицита из фондов", u.pokrytie_komu)
if u.kapital_dohod:
    stroka("+ доход на имущественный пай", u.dohod_na_pai)
# Считаем ровно тем же, чем считает ядро. Своя копия формулы забывала
# покрытие дефицита, и отчёт расходился с ядром на всю сумму покрытия.
lich = {k: R._lichnyy(u, k) for k in uch}
stroka("ЛИЧНЫЙ ДОХОД на руки", lich, True)
print()
# Прирост ЗА МЕСЯЦ, а не накопленный итог: при переносе остатков это разные
# числа, и строка показывала входящий остаток как заработанное за месяц.
stroka("Имущественный пай (прирост за месяц)",
       {k: u.imushch_pai[k] - u.pai_vhod[k] for k in uch}, True)
if any(u.pai_vhod[k] for k in uch):
    stroka("Имущественный пай (накоплено всего)", {k: u.imushch_pai[k] for k in uch})
print()

# --- 5. Материальные затраты
print("## 5. Материальные затраты: кто и сколько заплатил\n")
oz = dict(mes.get("obshchie_zatraty", {}))
for _imya, _sum in mes.get("karmany", par.get("karmany", {})).items():
    oz[f"карман: {_imya}"] = _sum
platyat = [k for k in uch if k not in deti]
print("| Статья | Сумма |")
print("|---|---:|")
for st, summa in oz.items():
    print(f"| {st} | {s(summa)} |")
print(f"| **Итого** | **{s(sum(oz.values()))}** |\n")
print("Делятся между взрослыми **пропорционально их доходу до затрат** — дети не платят:\n")
print("| Взрослый | Заплатил | Доля |")
print("|---|---:|---:|")
for k in platyat:
    print(f"| {k} | {s(u.zatraty[k])} | {u.zatraty[k]/sum(oz.values())*100:.1f} % |")
print()

if u.karmany:
    print("### Карманы: копится на то, что покупается рывками\n")
    print("| Карман | Отложено на конец месяца | Покупки этого месяца |")
    print("|---|---:|---|")
    pok = defaultdict(list)
    for x in mes.get("pokupki_iz_karmanov", []):
        pok[x["karman"]].append(f"{x.get('chto','покупка')} ({s(x['summa'])})")
    for k, v in u.karmany.items():
        print(f"| {k} | {s(v)} | {'; '.join(pok.get(k, [])) or '—'} |")
    print("\nПокупка из кармана тратит деньги, отложенные в прошлые месяцы, "
          "и на расчёт этого месяца не влияет.\n")

# --- 5б. Капитал
if u.kapital_dohod or u.pai_vhod or u.fondy_vhod:
    print("## 5а. Капитал: что накоплено, где лежит и что принесло\n")
    vhod_pai = sum(u.pai_vhod.values()); vhod_fon = sum(u.fondy_vhod.values())
    print(f"На начало месяца: имущественные паи **{s(vhod_pai)}**, "
          f"остатки фондов **{s(vhod_fon)}**, всего **{s(vhod_pai + vhod_fon)}**.\n")
    reestr = R._chitat(os.path.join(HERE, mes.get("aktivy", "aktivy.json")))
    if reestr.get("aktivy"):
        print("| Актив | Вид | Сумма | Ставка | Принёс за месяц |")
        print("|---|---|---:|---:|---:|")
        razmeshcheno = 0.0
        for a in reestr["aktivy"]:
            d = a.get("summa", 0.0) * a.get("stavka_godovyh", 0.0) / 12.0
            razmeshcheno += a.get("summa", 0.0) if a.get("stavka_godovyh", 0.0) else 0.0
            print(f"| {a['nazvanie']} | {a.get('vid','—')} | {s(a.get('summa',0))} | "
                  f"{a.get('stavka_godovyh',0)*100:.1f} % | {s(d)} |")
        print(f"| **Итого** | | **{s(sum(a.get('summa',0) for a in reestr['aktivy']))}** | | "
              f"**{s(u.kapital_dohod)}** |\n")
        ne_rab = sum(a.get('summa',0) for a in reestr['aktivy'] if not a.get('stavka_godovyh',0))
        if ne_rab:
            print(f"Не работает: **{s(ne_rab)}** — лежит без ставки и дохода не приносит.\n")
    if u.kapital_dohod:
        print("Доход разнесён так:\n")
        print("| Куда | Сумма | Основание |")
        print("|---|---:|---|")
        print(f"| Доля управления | {s(u.kapital_upravlenie)} | "
              f"{par['dohod_na_kapital']['dolya_upravleniya']*100:.0f} % — семья держит и обслуживает капитал |")
        for k, v in u.dohod_na_pai.items():
            if v:
                print(f"| {k} — на имущественный пай | {s(v)} | доля в паях |")
        for f, v in u.dohod_fondam.items():
            if v:
                print(f"| {IMENA.get(f,f)} — на остаток фонда | {s(v)} | доля в остатках |")
        print(f"| **Итого** | **{s(u.kapital_dohod)}** | |\n")
        print("Доход на имущественный пай **не делится по формуле 50/50**: это доход "
              "на собственность, а не выручка от труда. Допущение — первоисточник молчит.\n")

# --- 5б. Минимум взноса
if u.min_doplata or u.min_nedobor:
    cfg = par["minimum_nakopleniya"]
    print("## 5в. Минимум взноса в накопление\n")
    print(f"Накопление — обязанность, а не остаток: не меньше **{cfg['znachenie']*100:.0f} % выручки** "
          f"({cfg['primenyaetsya_k']}). Шкала Р-7 в этом месяце дала меньше, поэтому включился минимум.\n")
    print("| Участник | Требовалось | Доплатил | Не добрал | Срезано дивидендов |")
    print("|---|---:|---:|---:|---:|")
    for kto in uch:
        if u.min_doplata[kto] or u.min_nedobor[kto]:
            print(f"| {kto} | {s(u.min_trebuetsya[kto])} | {s(u.min_doplata[kto])} | "
                  f"{s(u.min_nedobor[kto])} | {s(u.sankciya[kto])} |")
    print("\nСанкция за невыполнение — урезание земельных дивидендов в той же доле, "
          "срезанное делится между выполнившими. Так у Чартаева урезали земельный пай.\n")

# --- 5г. Дефицит
if u.deficit or u.dolg or u.pogasheno or u.dolg_vhod:
    print("## 5б. Дефицит и заём\n")
    # Берём минимум ЭТОГО месяца, а не фиксированное значение из нормативов:
    # при режиме «из месяца» они расходятся на всю разницу тарифов.
    pm = u.prozh_minimum
    if u.deficit:
        print(f"Прожиточный минимум месяца — **{s(pm)}**. Не хватило **{s(u.deficit)}**, "
              f"это **{u.mes_deficita}-й месяц подряд** при пределе "
              f"{par['deficit']['podryad_mesyacev_do_ostanovki']}.\n")
        print("| Чем закрыт | Сумма | Возврат |")
        print("|---|---:|---|")
        for f, v in u.pokryto.items():
            vozvr = "**заём, вернуть из накопления**" if f in par["deficit"].get("vozvratnye", []) else "не возвращается"
            print(f"| {IMENA.get(f, f)} | {s(v)} | {vozvr} |")
        if u.nepokryto:
            print(f"| **Не покрыто — легло на взрослых** | **{s(u.nepokryto)}** | режется пропорционально |")
        print()
        if u.pokrytie_komu:
            print("Кому пришло покрытие: " +
                  ", ".join(f"{k} {s(v)}" for k, v in u.pokrytie_komu.items() if v) + ".\n")
    if u.pogasheno:
        print(f"Погашено долга из инвестиционного фонда: **{s(u.pogasheno)}** — "
              f"на столько меньше ушло в имущественные паи этого месяца.\n")
    print(f"Долг резервному фонду: было {s(u.dolg_vhod)} → стало **{s(u.dolg)}**.\n")
    print("Имущественные паи на покрытие дефицита не трогаются ни при каких условиях: "
          "это личная собственность участника.\n")

# --- 6. Сверка
print("## 6. Сверка: куда ушли реальные деньги\n")
zatr = sum(u.zatraty.values())
pai = sum(u.imushch_pai.values()) - sum(u.pai_vhod.values())
ost = ost - sum(u.fondy_vhod.values())
vsego_vnesh = vsego_vnesh + u.kapital_dohod
lsum = sum(lich.values())
if u.kapital_dohod:
    print(f"Приток месяца: {s(vsego_vnesh - u.kapital_dohod)} трудовой выручки "
          f"плюс {s(u.kapital_dohod)} дохода на капитал.\n")
# Отчисление в карман — это НЕ оплата наружу: деньги отложены и лежат в семье.
# Раньше карман входил в строку «оплачено наружу», и она показывала 1 400 при
# 1 300 реально ушедших, а строкой ниже те же 100 значились отложенными.
karman_mes = sum(mes.get("karmany", par.get("karmany", {})).values())
naruzhu = zatr - karman_mes
print("| Назначение | Сумма | Доля притока |")
print("|---|---:|---:|")
stroki = [("На руки участникам (личные доходы)", lsum),
          ("Оплачено наружу (еда, коммунальные)", naruzhu)]
if karman_mes:
    stroki.append(("Отложено в карманы (не ушло из семьи)", karman_mes))
stroki += [("Осело в фондах", ost), ("Записано в имущественные паи", pai)]
for nazv, v in stroki:
    print(f"| {nazv} | {s(v)} | {v/vsego_vnesh*100:.0f} % |")
print(f"| **Итого** | **{s(lsum + zatr + ost + pai)}** | **100 %** |\n")
print(f"Пришло извне {s(vsego_vnesh)} — разложилось {s(lsum + zatr + ost + pai)}. "
      + ("Сходится." if abs(lsum + zatr + ost + pai - vsego_vnesh) < 0.01 else "**РАСХОЖДЕНИЕ.**"))
print(f"\nЛежит без движения (фонды + паи): **{s(ost + pai)}**, это "
      f"{(ost + pai)/vsego_vnesh*100:.0f} % притока. Семья живёт на {s(lsum + zatr)}.\n")
print("## 7. Что из этого следует\n")
platelshchik = max(kupil, key=kupil.get) if kupil else None
if platelshchik and kupil[platelshchik] > 0:
    print(f"**{platelshchik} отдаёт {s(kupil[platelshchik])} внутри семьи** — это самая крупная "
          f"статья после отчислений, и именно она превращает заработок в доходы остальных.\n")
# Утверждение проверяется по факту: раньше печаталось «не тронуты» на месяце,
# где §5б строкой выше сообщал о снятых из страхового деньгах.
_tronuto = u.fondy.rashod['rezerv'] + u.fondy.rashod['strahovoy']
if _tronuto > 1e-9:
    print(f"**Резервный и страховой фонды за месяц тронуты на {s(_tronuto)}** "
          f"(покрытие дефицита или заём), осталось "
          f"{s(u.fondy['rezerv'] + u.fondy['strahovoy'])}.\n")
else:
    print(f"**Резервный и страховой фонды за месяц не тронуты** — {s(u.fondy['rezerv'] + u.fondy['strahovoy'])} "
          f"просто легли. Так будет каждый месяц, пока не задано, при каких условиях из них платят.\n")
print(f"**Дети получили дивиденды наравне со взрослыми** — по {s(u.dividendy[uch[0]])} каждому. "
      f"Это работа земельного пая: доход по праву членства, не связанный с результатом.\n")
print(f"**Лежит без движения {s(ost + pai)} — {(ost + pai)/vsego_vnesh*100:.0f} % притока.** "
      f"Это прямое следствие правила 50/50. У Чартаева накоплению было куда работать: оборот, "
      f"закупка, техника. С 17.08.2026 такой ход есть и здесь — реестр активов и доход "
      f"на имущественный пай (`aktivy.json`), но работает он только с реально размещённого. "
      f"Прежде чем писать приложение, стоит прожить так один настоящий месяц и посмотреть, "
      f"хватает ли семье {s(lsum + zatr)}.\n")
print("---\n")
print("**На заглушках, требует ваших чисел:** " + "; ".join(par.get("_zaglushki", [])) + ".")
if par.get("_dopushcheniya"):
    print("\n**Допущения — наши решения, не первоисточник:**\n")
    for d in par["_dopushcheniya"]:
        print(f"- {d}")
