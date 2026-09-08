#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Расчётное ядро семейного учёта по системе Чартаева — черновик от 16.08.2026.

Считает один месяц по правилам из parametry.json. Правила здесь не зашиты:
программа умеет только «разделить выручку участника и разнести по субсчетам»,
а какие доли и куда — берётся из файла.

Принятые решения (16.08.2026, владелец):
  * это ВНУТРЕННИЙ семейный учёт, не юридическое лицо;
  * зарплата — ВЫРУЧКА УЧАСТНИКА, то есть делится по общей формуле, как любая
    другая выручка.

Запуск:  python3 raschet.py [parametry.json] [mesyac.json] [--sravnit]
"""
import json, sys, os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def _chitat(put):
    """Необязательный источник: имя файла ИЛИ уже готовый словарь.

    Словарём пользуется кабинет: он сцепляет месяцы между собой и передаёт
    остатки предыдущего месяца прямо, без промежуточного файла. Нет источника —
    считаем, что пусто, тогда старые прогоны не ломаются.
    """
    if isinstance(put, dict):
        return put
    if put and os.path.exists(put):
        return json.load(open(put, encoding="utf-8"))
    if put:
        # Имя задано, файла нет — почти всегда опечатка. Раньше возвращался
        # молчаливый ноль, и весь перенос исчезал без единого слова.
        raise SystemExit(f"файл «{os.path.basename(str(put))}» указан, но не найден "
                         f"({put}) — проверьте имя")
    return {}


def zagruzit(par_path, mes_path):
    par = json.load(open(par_path, encoding="utf-8"))
    mes = json.load(open(mes_path, encoding="utf-8"))
    pm_cfg = par.get("prozhitochnyy_minimum", {})
    if pm_cfg.get("rezhim") == "fiksirovannyy" and not par["norma_nakopleniya"].get(
            "stupeni_v_kratnostyah_minimuma"):
        st0 = par["norma_nakopleniya"]["stupeni"][0]
        if st0["dolya"] == 0 and abs((st0["do"] or 0) - pm_cfg.get("summa", 0)) > 1e-9:
            raise SystemExit(f"нулевая ступень шкалы кончается на {st0['do']}, а прожиточный "
                             f"минимум {pm_cfg.get('summa')} — расхождение, расчёт остановлен")
    s = sum(par["nakoplenie"].values())
    polnaya = par["norma_nakopleniya"]["polnaya_norma"]
    if abs(s - polnaya) > 1e-9:
        raise SystemExit(f"нормативы накопления дают {s:.4f}, а полная норма "
                         f"{polnaya:.4f} — расчёт остановлен")
    # P2-3: если получатель доли управления задан, он должен быть в составе —
    # иначе деньги оседают в мёртвом фонде без предупреждения (подтверждено:
    # при составе «папа/мама/дочь/сын» и poluchatel='Лена' теряется 275 ₽/мес).
    org = par.get("poluchatel_upravleniya")
    if org:
        sost = set(mes.get("uchastniki", []))
        if org not in sost:
            raise SystemExit(
                f"получатель доли управления «{org}» не входит в состав семьи "
                f"{sorted(sost)} — фонд управления накапливается и не распределяется. "
                f"Укажите участника из состава или оставьте поле пустым.")
    return par, mes


def prozhitochnyy_minimum(par, mes):
    """Сколько семье нужно в этом месяце на обязательное.

    Тарифы плавают: коммуналка, электричество, продукты. Поэтому по умолчанию
    величина считается ИЗ МЕСЯЦА, а не берётся константой. Ступени шкалы заданы
    в кратностях этой величины и сдвигаются вместе с ней.
    """
    cfg = par.get("prozhitochnyy_minimum", {})
    if cfg.get("rezhim") != "iz_mesyaca":
        return cfg.get("summa", 0.0)
    obyaz = sum(mes.get("obshchie_zatraty", {}).values())
    karmany = mes.get("karmany", par.get("karmany", {}))
    return obyaz + sum(karmany.values())


def norma_po_dohodu(par, vneshniy_dohod, pm=None):
    """Норма накопления пляшет от дохода семьи по ступеням.

    Доля берётся не со всей суммы, а с части дохода, попавшей в ступень, — как
    в подоходном налоге. Это единственный способ выполнить сразу три условия:
    при 4000 ровно 50/50, при доходе на уровне обязательных затрат не копим
    вовсе, и «заработал больше — на руки больше» на всём диапазоне.
    """
    nn = par["norma_nakopleniya"]
    k = pm if (nn.get("stupeni_v_kratnostyah_minimuma") and pm) else 1.0
    ostatok, nakop = vneshniy_dohod, 0.0
    for st_ in nn["stupeni"]:
        st = {"ot": st_["ot"] * k,
              "do": None if st_["do"] is None else st_["do"] * k,
              "dolya": st_["dolya"]}
        if ostatok <= st["ot"]:
            break
        verh = st["do"] if st["do"] is not None else float("inf")
        chast = min(ostatok, verh) - st["ot"]
        if chast > 0:
            nakop += chast * st["dolya"]
    return 0.0 if vneshniy_dohod <= 0 else nakop / vneshniy_dohod


class Schet(defaultdict):
    """Личный счёт участника. Начисление человеку вне состава не проходит молча.

    Раньше `u.upravlenie["Лена"] += x` при отсутствии «Лены» в семье создавало
    ключ, деньги оседали в невидимом счёте и выпадали из сходимости. Теперь
    каждая такая попытка записывается в `mimo` и деньги на счёт НЕ попадают —
    вызывающий обязан решить, что с ними делать.
    """

    def __init__(self, sostav, imya_scheta):
        super().__init__(float)
        self._sostav = sostav
        self._imya = imya_scheta
        self.mimo = defaultdict(float)      # кому пытались начислить мимо состава

    def __setitem__(self, k, v):
        if k not in self._sostav:
            self.mimo[k] += v - dict.get(self, k, 0.0)
            return                          # на счёт не пускаем
        super().__setitem__(k, v)

    # Щит только на __setitem__ дырявый: update и setdefault пишут мимо него.
    def update(self, *a, **kw):
        tmp = dict(*a, **kw)
        for k, v in tmp.items():
            self[k] = v

    def setdefault(self, k, default=0.0):
        if k not in self:
            self[k] = default
        return dict.get(self, k, default)

    def copy(self):
        # defaultdict.copy зовёт type(self)(default_factory, mapping) — наша
        # сигнатура это съедала, и счёт возвращался пустым. Возвращаем обычный dict.
        return dict(self)


class Fondy(defaultdict):
    """Фонды с автоматическим учётом движения.

    Любое изменение остатка попадает в prihod или rashod само, где бы в коде оно
    ни случилось. Поэтому тождество «вход + приход − расход = остаток» держится
    ПО ПОСТРОЕНИЮ, а не по договорённости между местами расчёта. До этого приход
    считался заново формулой «выручка × норматив» в кабинете и в отчёте — и
    расходился с ядром на доплату минимума, дефицит и входящие остатки.
    """

    def __init__(self):
        super().__init__(float)
        self.prihod = defaultdict(float)
        self.rashod = defaultdict(float)
        self.uchet_idet = False          # пока грузим входящие остатки — это не движение

    def __setitem__(self, k, v):
        if self.uchet_idet:
            delta = v - dict.get(self, k, 0.0)
            if delta >= 0:
                self.prihod[k] += delta
            else:
                self.rashod[k] += -delta
        super().__setitem__(k, v)

    def sverka(self, vhod):
        """[(фонд, невязка)] — где вход + приход − расход не равны остатку."""
        plohie = []
        for f in set(list(self) + list(self.prihod) + list(self.rashod) + list(vhod)):
            nevyazka = (vhod.get(f, 0.0) + self.prihod[f]
                        - self.rashod[f] - dict.get(self, f, 0.0))
            # Порог относительный: у абсолютного 1e-9 на суммах выше ~50 млн
            # шум double выглядел как утечка и пугал человека предупреждением.
            masshtab = max(abs(vhod.get(f, 0.0)), abs(self.prihod[f]),
                           abs(self.rashod[f]), abs(dict.get(self, f, 0.0)), 1.0)
            if abs(nevyazka) > 1e-9 * masshtab:
                plohie.append((f, nevyazka))
        return plohie


class Uchet:
    """Журнал: у каждого участника личные счета, у семьи — фонды."""

    def __init__(self, par, uchastniki, norma=None):
        self.par = par
        polnaya = par["norma_nakopleniya"]["polnaya_norma"]
        self.norma = polnaya if norma is None else norma
        k = 0.0 if polnaya == 0 else self.norma / polnaya
        self.nak_doli = {f: d * k for f, d in par["nakoplenie"].items()}
        # УСЛОВИЕ СОХРАНЕНИЯ ДЕНЕГ, проверяемое ровно там, где считается.
        # Из потребления вычитается норма, а в субсчета кладётся сумма их долей.
        # Не равны — разница берётся из ниоткуда или исчезает: при долях в половину
        # нормы пропадала 1 000 из 4 000, при удвоенных появлялись 2 000.
        # Такая проверка была только в zagruzit(), а кабинет её не зовёт вовсе.
        self.zametki = []
        summa_doley = sum(self.nak_doli.values())
        if abs(summa_doley - self.norma) > 1e-9:
            raise SystemExit(
                f"доли субсчетов накопления дают {summa_doley:.4f}, а норма "
                f"накопления {self.norma:.4f} — расчёт остановлен: разница "
                f"{self.norma - summa_doley:+.4f} от каждого рубля выручки взялась бы "
                f"из ниоткуда. Сумма нормативов «nakoplenie» должна равняться "
                f"«polnaya_norma» ({polnaya:.4f})")
        self.fondy = Fondy()                         # накопительные фонды семьи (с самоучётом движения)
        self.uchastniki = list(uchastniki)
        sost = set(self.uchastniki)
        self.vyruchka = Schet(sost, "выручка")       # выручка участника за месяц
        self.potreblenie = Schet(sost, "потребление")  # его доля потребления
        self.imushch_pai = Schet(sost, "имущественный пай")
        self.dividendy = Schet(sost, "дивиденды")    # доход по земельным паям
        self.upravlenie = Schet(sost, "доля управления")  # доход организатора
        self.zatraty = Schet(sost, "затраты")        # материальные затраты участника
        self.vneshniy_pritok = 0.0                   # реальные деньги извне
        self.vneshniy_rashod = 0.0                   # реальные деньги наружу
        self.vnutrenniy_oborot = 0.0                 # перекладывание внутри семьи
        self.zhurnal = []
        self.preduprezhdeniya = []               # то, что нельзя решать за человека
        self.karmany = defaultdict(float)        # накопленное на нерегулярные обязательные траты
        self.karmany_vhod = defaultdict(float)   # остатки карманов на начало месяца
        self.pai_vhod = defaultdict(float)       # имущественный пай на НАЧАЛО месяца
        self.fondy_vhod = defaultdict(float)     # остатки фондов на начало месяца
        self.dohod_na_pai = defaultdict(float)   # доход участника на его имущественный пай
        self.dohod_fondam = defaultdict(float)   # доход, начисленный на остатки фондов
        self.kapital_dohod = 0.0                 # весь доход с размещённых активов за месяц
        self.kapital_upravlenie = 0.0            # доля управления, снятая с этого дохода
        self.vyruchka_total = 0.0                # база для долей: vyruchka после вычета внутр. продаж
        self.deficit = 0.0                       # сколько не хватило на обязательное
        self.pokryto = defaultdict(float)        # чем закрыт дефицит: фонд -> сумма
        self.pokrytie_komu = defaultdict(float)  # кому пришло покрытие
        self.dolg_vhod = 0.0                     # долг резервному фонду на начало месяца
        self.dolg = 0.0                          # долг на конец месяца
        self.pogasheno = 0.0                     # погашено долга в этом месяце
        self.mezhdu_fondami = 0.0                # приход фондов, который НЕ из выручки:
                                                 # перекладывание между фондами и возврат
                                                 # уже розданных денег обратно в фонд
        self.mes_deficita = 0                    # месяцев дефицита подряд, включая этот
        self.nepokryto = 0.0                     # остаток дефицита, легший на людей
        self.prozh_minimum = 0.0                 # прожиточный минимум ЭТОГО месяца
        self.min_trebuetsya = defaultdict(float) # минимум взноса в накопление
        self.min_doplata = defaultdict(float)    # доплачено, чтобы дотянуть до минимума
        self.min_nedobor = defaultdict(float)    # не дотянул — идёт санкция
        self.sankciya = defaultdict(float)       # срезано земельных дивидендов

    # --- главная операция: пришла выручка участнику ---
    def vyruchka_uchastnika(self, kto, summa, istochnik):
        self.vyruchka[kto] += summa
        nak = summa * self.norma
        self.potreblenie[kto] += summa - nak
        for fond, dolya in self.nak_doli.items():
            self.fondy[fond] += summa * dolya
        self.zhurnal.append((istochnik, kto, summa, nak, summa - nak))

    def pokupka_dolya(self, pokupatel, prodavec, dolya, chto):
        """Цена задана долей фонда потребления покупателя — она сама пляшет от дохода."""
        summa = max(0.0, self.potreblenie[pokupatel]) * dolya
        self.pokupka_u_uchastnika(pokupatel, prodavec, summa, chto)

    def rashod_fonda_dolya(self, fond, dolya, komu, chto):
        """Цена задана долей фонда: фонд делится между получателями и не уходит в минус."""
        self.rashod_fonda(fond, max(0.0, self.fondy[fond]) * dolya, komu, chto)

    def pokupka_u_uchastnika(self, pokupatel, prodavec, summa, chto):
        """Один участник покупает результат у другого — из своего потребления."""
        self.potreblenie[pokupatel] -= summa
        self.vnutrenniy_oborot += summa
        self.vyruchka_uchastnika(prodavec, summa, f"внутри: {chto}")

    def rashod_fonda(self, fond, summa, komu, chto):
        """Из фонда нельзя заплатить больше, чем в нём есть.

        Раньше остаток уходил в минус, и минус растекался дальше: отрицательный
        земельный фонд раздавался отрицательными дивидендами, санкция от
        отрицательной базы ПРИБАВЛЯЛА получателю, а обнуление отрицательного
        инвестфонда записывалось приходом. На нетронутых нормативах это давало
        +50.00 из воздуха при выплате 100.00 из пустого фонда земельных паёв.
        """
        est = max(0.0, self.fondy[fond])
        if summa > est + 1e-9:
            self.preduprezhdeniya.append(
                f"выплата «{chto}» из фонда «{fond}»: запрошено {summa:.2f}, "
                f"в фонде {est:.2f} — выплачено {est:.2f}, остальное не выплачено")
            summa = est
        if summa <= 1e-9:
            return
        self.fondy[fond] -= summa
        self.vnutrenniy_oborot += summa
        self.vyruchka_uchastnika(komu, summa, f"внутри: {chto}")


def _mimo_sostava(u):
    """Собрать всё, что пытались начислить людям вне состава, и сказать вслух.

    Без этого щит `Schet` работал вполуха: деньги не доходили до постороннего,
    но и ни до кого другого, а человек об этом не узнавал.
    """
    itog = defaultdict(float)
    for nazv in ("vyruchka", "potreblenie", "imushch_pai", "dividendy",
                 "upravlenie", "zatraty"):
        sch = getattr(u, nazv, None)
        for kto, v in getattr(sch, "mimo", {}).items():
            if abs(v) > 1e-9:
                itog[kto] += v
    for kto, v in sorted(itog.items()):
        u.preduprezhdeniya.append(
            f"{v:.2f} не начислено: «{kto}» не входит в состав семьи")
    return dict(itog)


def _skazat_pro_otricatelnye(u, otric, gde):
    """Сказать вслух, чей вес был отброшен как отрицательный."""
    for kto, v in sorted(otric.items()):
        u.preduprezhdeniya.append(
            f"при {gde} выручка «{kto}» отрицательна ({v:.2f}) — доля считается "
            f"нулевой: отрицательная доля отняла бы у него уже накопленное")


def raznesti(summa, vesa):
    """Разнести сумму пропорционально весам. Возвращает (доли, получилось_ли).

    Если сумма весов ноль — разносить НЕ ПО ЧЕМУ, и функция говорит об этом,
    а не выдумывает знаменатель. Идиома `X or 1.0`, стоявшая на четырёх местах
    этого файла, делала именно это: подменяла нулевой знаменатель единицей, все
    доли получались нулевыми, и разносимая сумма молча исчезала — из фонда,
    из затрат, из паёв. Приёмка нашла два таких места, третье нашлось грепом.

    Вызывающий обязан решить, что делать при `False`: разделить поровну,
    оставить на следующий месяц или остановить расчёт. Молча потерять — нельзя.

    Отрицательный вес — не доля, а долг. Участник с отрицательной выручкой не
    может получить ОТРИЦАТЕЛЬНУЮ долю фонда: это молча отнимало у него уже
    накопленный пай (проверено: инвестфонд положительный, а пай мамы −300.01)
    и делало отрицательными его затраты. Такие веса считаются нулевыми,
    а вызывающему сообщается, у кого именно, — решать ему.
    """
    otricatelnye = {k: v for k, v in vesa.items() if v < -1e-12}
    chistye = {k: max(0.0, v) for k, v in vesa.items()}
    baza = sum(chistye.values())
    if baza <= 1e-12:
        return {}, False, otricatelnye
    return {k: summa * v / baza for k, v in chistye.items()}, True, otricatelnye


def balans_na_sleduyushchiy(u, par):
    """Баланс на следующий месяц из посчитанного. ЕДИНСТВЕННЫЙ сборщик.

    Раньше их было два — в perenos.py и в кабинете — с расходящимся набором
    полей, и месяцы из файлов переносились не так, как месяцы из журнала.
    """
    pai = {k: u.imushch_pai[k] for k in u.uchastniki}
    # Все нормативные фонды пишем всегда, даже нулевые: пропажу строки видно
    # глазом при сравнении месяцев. Плюс всё, что реально есть в расчёте —
    # иначе фонд, которого нет в нормативах, исчезал на переносе (900.00 из
    # 1 100.00), и предупреждение об этом печаталось, но денег не спасало.
    kluchi = list(par["nakoplenie"]) + [f for f in u.fondy if f not in par["nakoplenie"]]
    fondy = {f: u.fondy[f] for f in kluchi}
    karmany = {k: v for k, v in u.karmany.items()}
    return {
        "_": "собран автоматически. Руками не править.",
        "imushchestvennye_pai": pai,
        "fondy_ostatki": fondy,
        "karmany_ostatki": karmany,
        "dolg_rezervu": u.dolg,
        "mesyacev_deficita_podryad": u.mes_deficita,
        "_svodka": {"vsego": sum(pai.values()) + sum(fondy.values()) + sum(karmany.values()),
                    "pai": sum(pai.values()), "fondy": sum(fondy.values()),
                    "karmany": sum(karmany.values()),
                    "uchastnikov": len(u.uchastniki), "fondov": len(fondy)},
    }


def schitat(par, mes, kommunalka_v_nakoplenii=None):
    if kommunalka_v_nakoplenii is None:
        kommunalka_v_nakoplenii = par.get("kommunalka_v_nakoplenii", False)
    vnesh = sum(mes.get("vneshnyaya_vyruchka", {}).values())
    pm = prozhitochnyy_minimum(par, mes)
    # Норма накопления считается от ТРУДОВОЙ выручки. Доход на капитал в базу
    # нормы не входит: он заработан собственностью, а не трудом этого месяца.
    u = Uchet(par, mes["uchastniki"], norma_po_dohodu(par, vnesh, pm))
    u.prozh_minimum = pm
    u.preduprezhdeniya += u.zametki

    if not u.uchastniki:
        raise SystemExit("в составе семьи нет ни одного участника — считать нечего")

    # Состав проверяется ДО расчёта. Раньше строка на человека вне состава
    # проходила молча: печаталось расхождение сходимости, а расчёт продолжался,
    # и деньги просто выпадали из итогов.
    sost = set(u.uchastniki)
    chuzhie = {}
    def _sobrat(imena, otkuda):
        for imya in imena:
            if imya and imya not in sost:
                chuzhie.setdefault(imya, set()).add(otkuda)
    _sobrat(mes.get("vneshnyaya_vyruchka", {}), "внешняя выручка")
    _sobrat(mes.get("lichnye_zatraty", {}), "личные затраты")
    for op in mes.get("vnutrennie_pokupki", []):
        _sobrat([op.get("ot_uchastnika"), op.get("komu")], "внутренние покупки")
    for kto in mes.get("deti", []) + mes.get("otdelno", []):
        _sobrat([kto], "дети/живут отдельно")
    if chuzhie:
        raise SystemExit(
            "в месяце есть записи на людей, которых нет в составе семьи: "
            + "; ".join(f"«{k}» ({', '.join(sorted(v))})" for k, v in sorted(chuzhie.items()))
            + ". Поправьте состав или записи — иначе их деньги выпадут из итогов.")

    # 0. Что накоплено к первому числу. Без запаса доход на капитал не считается.
    _bal_ist = mes.get("balans")
    bal = _chitat(_bal_ist if isinstance(_bal_ist, dict)
                  else os.path.join(HERE, _bal_ist)) if _bal_ist else {}
    for kto, v in bal.get("imushchestvennye_pai", {}).items():
        u.pai_vhod[kto] += v
        u.imushch_pai[kto] += v
    for f, v in bal.get("fondy_ostatki", {}).items():
        u.fondy_vhod[f] += v
        u.fondy[f] += v
    u.dolg_vhod = u.dolg = bal.get("dolg_rezervu", 0.0)
    u.mes_deficita = bal.get("mesyacev_deficita_podryad", 0)
    # Карманы тоже переходят между месяцами: в них лежат отложенные деньги.
    # Раньше их несла только строка в файле месяца, руками, и при переносе
    # они терялись целиком.
    for imya, v in bal.get("karmany_ostatki", {}).items():
        u.karmany_vhod[imya] += v
        u.karmany[imya] += v
    # Сверка при чтении: баланс сам несёт контрольную сумму того, что в нём
    # записано. Раньше баланс переносился руками с округлённых значений, и
    # пропажу целого фонда никто не замечал.
    # Баланс — единственный канал переноса, и раньше он не проверялся вовсе:
    # посторонний, вписанный в паи, получал реальный доход на капитал.
    _chuzhie_bal = sorted(set(bal.get("imushchestvennye_pai", {})) - set(u.uchastniki))
    if _chuzhie_bal:
        raise SystemExit(
            "во входящем балансе есть паи людей вне состава семьи: "
            + ", ".join(f"«{k}»" for k in _chuzhie_bal)
            + ". Поправьте состав или баланс — иначе их деньги выпадут из итогов.")
    _lishnie_f = sorted(set(bal.get("fondy_ostatki", {})) - set(par["nakoplenie"]))
    if _lishnie_f:
        u.preduprezhdeniya.append(
            "во входящем балансе фонды, которых нет в нормативах: "
            + ", ".join(_lishnie_f) + " — при переносе они исчезнут")
    sv = bal.get("_svodka", {})
    if bal and not sv:
        u.preduprezhdeniya.append(
            "во входящем балансе нет контрольной суммы (_svodka): проверить, "
            "всё ли перенесено, невозможно — пересоберите его через perenos.py")
    if sv:
        bylo = (sum(bal.get("imushchestvennye_pai", {}).values())
                + sum(bal.get("fondy_ostatki", {}).values())
                + sum(bal.get("karmany_ostatki", {}).values()))
        if abs(bylo - sv.get("vsego", bylo)) > 0.005:
            u.preduprezhdeniya.append(
                f"баланс на входе не сходится со своей контрольной суммой: "
                f"строки дают {bylo:.2f}, записано {sv.get('vsego'):.2f} — "
                f"часть остатков потерялась при переносе")
    # Входящие остатки разложены — дальше всё, что двигает фонды, считается движением.
    u.fondy.uchet_idet = True

    # 1. Внешняя выручка (по принятому решению — зарплата тоже выручка).
    for kto, summa in mes.get("vneshnyaya_vyruchka", {}).items():
        u.vneshniy_pritok += summa
        u.vyruchka_uchastnika(kto, summa, "зарплата")

    # 2. Внутренние покупки: фонд покупает результат у участника.
    #    Это снова выручка — она опять делится по формуле (см. вывод про оборот).
    for op in mes.get("vnutrennie_pokupki", []):
        if op.get("ot_uchastnika"):
            if "dolya_potrebleniya" in op:
                u.pokupka_dolya(op["ot_uchastnika"], op["komu"], op["dolya_potrebleniya"], op["chto"])
            else:
                u.pokupka_u_uchastnika(op["ot_uchastnika"], op["komu"], op["summa"], op["chto"])
        elif "dolya_fonda" in op:
            u.rashod_fonda_dolya(op["iz_fonda"], op["dolya_fonda"], op["komu"], op["chto"])
        else:
            u.rashod_fonda(op["iz_fonda"], op["summa"], op["komu"], op["chto"])

    # 3. Дивиденды по земельным паям — поровну всем по праву членства.
    zem = u.fondy["zemelnye_pai"]
    if zem > 1e-9:                     # отрицательный фонд раздавать нельзя
        dolya = zem / len(u.uchastniki)
        for kto in u.uchastniki:
            u.dividendy[kto] += dolya
        u.fondy["zemelnye_pai"] = 0.0

    # 4. Доля управления — организатору быта.
    org = par.get("poluchatel_upravleniya")
    if org and u.fondy["upravlenie"]:
        if org in u.uchastniki:
            u.upravlenie[org] += u.fondy["upravlenie"]
            u.fondy["upravlenie"] = 0.0
        else:
            # Получатель доли управления не в составе — раньше деньги начислялись
            # человеку, которого нет, и молча выпадали из сходимости. Теперь
            # остаются в фонде и о них говорится вслух.
            u.preduprezhdeniya.append(
                f"доля управления {u.fondy['upravlenie']:.2f} осталась в фонде: "
                f"получатель «{org}» не входит в состав семьи")

    # 5. Инвестиционный фонд разносится в паи НЕ ЗДЕСЬ, а в самом конце (_finish):
    #    сначала надо узнать, не дефицитный ли месяц. В дефицитный из этого фонда
    #    гасится долг резерву, и разносить его в паи до того нельзя.
    # 5б. Доход на капитал (Р-Б по сверке 17.08.2026).
    #     У Чартаева дивиденды начисляются на ОБА пая, а не только на земельный.
    #     Начисляем строго с реально размещённого: реестр активов говорит, где
    #     лежат деньги и под какой процент. Не размещено — дохода нет.
    kap = par.get("dohod_na_kapital", {})
    if kap.get("vklyuchen"):
        _akt = mes.get("aktivy", "aktivy.json")
        reestr = _chitat(_akt if isinstance(_akt, dict) else os.path.join(HERE, _akt))
        dohod = 0.0
        for a in reestr.get("aktivy", []):
            dohod += a.get("summa", 0.0) * a.get("stavka_godovyh", 0.0) / 12.0
        if dohod > 0:
            u.kapital_dohod = dohod
            u.vneshniy_pritok += dohod          # это реальные деньги извне
            # Семья управляет капиталом и берёт за это долю — тот же норматив
            # управления, что у Чартаева на аппарат: 5 %.
            org = par.get("poluchatel_upravleniya")
            upr = dohod * kap.get("dolya_upravleniya", 0.0)
            if org and upr and org in u.uchastniki:
                u.kapital_upravlenie = upr
                u.upravlenie[org] += upr
            elif upr:
                # Тот же щит, что в шаге 4. Ветка ловит два случая: получатель вне
                # состава и получатель не задан вовсе. Второй раньше проваливался
                # мимо обоих условий, и доля управления терялась насовсем.
                prichina = (f"получатель «{org}» не входит в состав семьи" if org
                            else "получатель доли управления не задан")
                u.preduprezhdeniya.append(
                    f"доля управления с дохода на капитал {upr:.2f} не начислена: "
                    f"{prichina} — сумма осталась в разносе")
                upr = 0.0
            ostatok = dohod - upr
            baza_pai = sum(u.pai_vhod.values())
            baza_fon = sum(v for v in u.fondy_vhod.values())
            baza = baza_pai + baza_fon
            if baza > 0:
                # Доход на имущественный пай — владельцу пая, а не в общий котёл.
                for kto, v in u.pai_vhod.items():
                    if v:
                        u.dohod_na_pai[kto] += ostatok * v / baza
                # Доход на остатки фондов — обратно в фонды. ДОПУЩЕНИЕ: у
                # первоисточника про это не сказано ничего.
                for f, v in u.fondy_vhod.items():
                    if v:
                        dolya = ostatok * v / baza
                        u.dohod_fondam[f] += dolya
                        u.fondy[f] += dolya
            elif ostatok:
                # Ни входящих паёв, ни входящих остатков фондов — разносить доход
                # НЕ ПО ЧЕМУ. Так выглядит первый месяц любой цепочки и любой месяц,
                # где реестр активов заполнен, а баланс ещё пуст. Раньше остаток
                # не доставался никому и исчезал (проверено: из 1 200 пропадали
                # 1 140). Кладём в инвестиционный фонд — он и есть накопление,
                # которое разнесётся в паи, как только появится выручка.
                u.dohod_fondam["investicii"] += ostatok
                u.fondy["investicii"] += ostatok
                u.preduprezhdeniya.append(
                    f"доход на капитал {ostatok:.2f} положен в инвестиционный фонд: "
                    f"на начало месяца нет ни паёв, ни остатков фондов — разносить "
                    f"по владельцам не по чему (реестр активов не сходится с балансом)")
            if kap.get("dohod_na_pai_delitsya"):
                # Отклонённый по умолчанию вариант: гнать доход через общую
                # формулу. Тогда половина уйдёт обратно в накопление.
                for kto, v in list(u.dohod_na_pai.items()):
                    u.dohod_na_pai[kto] = 0.0
                    u.vyruchka_uchastnika(kto, v, "доход на имущественный пай")

    # 5а. Карманы: равномерные отчисления на нерегулярные обязательные траты
    #     (одежда, обувь, учебники). Это амортизация: вещь покупается разом,
    #     а списывается помесячно, иначе месяц покупки ломает расчёт всем.
    karmany = mes.get("karmany", par.get("karmany", {}))
    # Входящие карманы приходят либо из баланса, либо старым полем в месяце.
    # Оба разом — это задвоение: карман становился 300 вместо 200.
    _iz_balansa = set(u.karmany_vhod)
    for imya, ostatok in mes.get("karmany_vhodyashchie", {}).items():
        if imya in _iz_balansa:
            u.preduprezhdeniya.append(
                f"карман «{imya}» задан и в балансе, и полем karmany_vhodyashchie — "
                f"взят баланс, поле проигнорировано (уберите его из файла месяца)")
            continue
        u.karmany_vhod[imya] += ostatok
        u.karmany[imya] += ostatok
    for imya, mesyachnoe in karmany.items():
        u.karmany[imya] += mesyachnoe
    for pok in mes.get("pokupki_iz_karmanov", []):
        # Тратятся деньги, отложенные в прошлые месяцы, — на баланс этого месяца
        # покупка не влияет, поэтому во внешний расход месяца она не идёт.
        u.karmany[pok["karman"]] -= pok["summa"]

    # 6. Материальные затраты.
    obshch = dict(mes.get("obshchie_zatraty", {}))
    # Ключи карманов имеют префикс «карман: », но если он совпадёт с ключом
    # в obshchie_zatraty — второй цикл перезапишет его без предупреждения.
    # Сценарий: человек случайно занёс «карман: одежда и обувь детям: 50» в затраты
    # и «одежда и обувь детям: 100» в карманы → участники платят 1450 вместо 1350.
    # Проверка 1: общие затраты содержат ключ с префиксом «карман: », который
    # совпадает с карманом по имени (статья «карман: одежда» + карман «одежда»).
    karmany_keys = {f"карман: {imya}" for imya in karmany}
    peresek = set(obshch) & karmany_keys
    if peresek:
        for k in sorted(peresek):
            u.preduprezhdeniya.append(
                f"статья «{k}» есть и в общих затратах, и в карманах "
                f"({obshch[k]:.2f} + {karmany[k[7:]]:.2f}) — в затраты взято "
                f"{karmany[k[7:]]:.2f}, сначала удалите её из общих затрат")
    # Проверка 2: bare-ключ (без префикса) в общих затратах совпадает с bare-именем
    # кармана — например, «одежда» в затратах и «одежда» в карманах. Без этой проверки
    # участники платят 1450 вместо 1350 (50+100=150 вместо 50, и 100 уходит в карман).
    peresek_bare = set(obshch) & set(karmany)
    if peresek_bare:
        for k in sorted(peresek_bare):
            u.preduprezhdeniya.append(
                f"статья «{k}» есть и в общих затратах ({obshch[k]:.2f}), "
                f"и в карманах ({karmany[k]:.2f}) — статья будет учтена как карман, "
                f"удалите её из общих затрат")
    for imya, mesyachnoe in karmany.items():
        obshch[f"карман: {imya}"] = mesyachnoe
    komm = obshch.pop("коммунальные", 0.0)
    if kommunalka_v_nakoplenii:
        # Из резерва нельзя заплатить больше, чем в нём есть — иначе фонд уходит
        # в минус, а минус растекается дальше (тот же корень, что у выплаты из
        # пустого фонда). Чего не хватило — платят участники обычным порядком.
        est = max(0.0, u.fondy["rezerv"])
        iz_fonda = min(komm, est)
        if iz_fonda > 1e-9:
            u.fondy["rezerv"] -= iz_fonda
            u.vneshniy_rashod += iz_fonda
        if komm - iz_fonda > 1e-9:
            u.preduprezhdeniya.append(
                f"коммунальные {komm:.2f}: из резервного фонда покрыто {iz_fonda:.2f}, "
                f"остаток {komm - iz_fonda:.2f} не покрыт — участники платят из своего кармана")
    else:
        obshch["коммунальные"] = komm      # это материальные затраты участников
    for kto, summa in mes.get("lichnye_zatraty", {}).items():
        u.zatraty[kto] += summa
    obshchaya_summa = sum(obshch.values())
    if obshchaya_summa:
        pravilo = par.get("obshchie_zatraty_delyatsya")
        if pravilo == "po_dohodu_vzroslyh":
            # Пропорционально тому, что у взрослого есть к моменту оплаты: потребление
            # плюс дивиденды и доля управления. Никто не платит больше, чем имеет.
            # Общие затраты дома несут взрослые, ЖИВУЩИЕ С СЕМЬЁЙ. Отделившийся
            # остаётся участником (выручка, фонды, паи), но за еду и коммуналку
            # общего дома не платит — Р-4 и экран состава обещают именно это.
            ne_platyat = set(mes.get("deti", [])) | set(mes.get("otdelno", []))
            vzr = [k for k in u.uchastniki if k not in ne_platyat]
            if not vzr:
                raise SystemExit("общие затраты платить некому: все дети или живут отдельно")
            baza = {k: max(0.0, u.potreblenie[k] + u.dividendy[k] + u.upravlenie[k]
                              + u.dohod_na_pai[k]) for k in vzr}
            vsego_baza = sum(baza.values())
            if vsego_baza <= 0:
                raise SystemExit("нечем платить общие затраты: у взрослых нулевое потребление")
            for kto in vzr:
                u.zatraty[kto] += obshchaya_summa * baza[kto] / vsego_baza
            return _finish(u, par, mes)
        deti = set(mes.get("deti", [])) | set(mes.get("otdelno", []))
        if pravilo == "porovnu":
            platyat = list(u.uchastniki)
        elif pravilo == "porovnu_krome_detey":
            platyat = [k for k in u.uchastniki if k not in deti]
        elif pravilo == "po_dole_vyruchki_krome_detey":
            platyat = "dolya_vzroslye"
        else:
            platyat = None                  # по доле выручки среди всех
        if platyat == "dolya_vzroslye":
            vzr = [k for k in u.uchastniki if k not in deti]
            if not vzr:
                raise SystemExit("некому платить общие затраты: взрослых в составе нет")
            doli, est, otric = raznesti(obshchaya_summa, {k: u.vyruchka[k] for k in vzr})
            _skazat_pro_otricatelnye(u, otric, "дележе общих затрат")
            if est:
                for kto, d in doli.items():
                    u.zatraty[kto] += d
            else:
                d = obshchaya_summa / len(vzr)
                for kto in vzr:
                    u.zatraty[kto] += d
                u.preduprezhdeniya.append(
                    f"общие затраты {obshchaya_summa:.2f} разделены между взрослыми "
                    f"поровну: выручки у взрослых в этом месяце нет")
        elif platyat is not None:
            if not platyat:
                raise SystemExit("некому платить общие затраты: все в детях")
            d = obshchaya_summa / len(platyat)
            for kto in platyat:
                u.zatraty[kto] += d
        else:
            doli, est, otric = raznesti(obshchaya_summa,
                                        {k: u.vyruchka[k] for k in u.uchastniki})
            _skazat_pro_otricatelnye(u, otric, "дележе общих затрат")
            if est:
                for kto, d in doli.items():
                    u.zatraty[kto] += d
            else:
                # Выручки нет ни у кого — разносить по доле не по чему. Делим
                # поровну: затраты реальны и кем-то оплачены, а дальше их
                # подхватит машина дефицита. Раньше здесь не платил никто.
                d = obshchaya_summa / len(u.uchastniki)
                for kto in u.uchastniki:
                    u.zatraty[kto] += d
                u.preduprezhdeniya.append(
                    f"общие затраты {obshchaya_summa:.2f} разделены поровну: "
                    f"выручки в этом месяце нет ни у кого, делить по доле не по чему")
    return _finish(u, par, mes)


def _finish(u, par, mes):
    """Хвост расчёта в одном месте: сперва дефицит и долг, потом капитализация.

    Порядок важен. Долг резервному фонду гасится из инвестиционного фонда, а он
    после капитализации превращается в личные паи участников — трогать их нельзя
    по правилу. Значит гасим до разноса.
    """
    _minimum(u, par, mes)
    _deficit(u, par, mes)
    inv = u.fondy["investicii"]
    if inv > 1e-9:                     # отрицательный фонд в паи не разносится
        doli, est, otric = raznesti(inv, {k: u.vyruchka[k] for k in u.uchastniki})
        _skazat_pro_otricatelnye(u, otric, "разносе инвестиционного фонда в паи")
        if est:
            for kto, d in doli.items():
                u.imushch_pai[kto] += d
            u.fondy["investicii"] = 0.0
        else:
            # Выручки нет ни у кого — капитализировать не по чему. Фонд НЕ
            # обнуляем: он перейдёт на следующий месяц и разнесётся тогда.
            # Раньше здесь фонд обнулялся, а в паи не попадало ничего — деньги
            # исчезали целиком (проверено: вход 5000, сходимость −5000).
            u.preduprezhdeniya.append(
                f"инвестиционный фонд {inv:.2f} не разнесён в паи: выручки в этом "
                f"месяце нет ни у кого — фонд переходит на следующий месяц")
    # Последним делом сказать вслух про всё, что пытались начислить мимо состава.
    _mimo_sostava(u)
    # vyruchka = личный доход (шаг 4). Инвестиционный доход (шаг 7) идёт в обход
    # vyruchka — напрямую в паи. vyruchka_total = личный доход (инвест-часть не здесь).
    u.vyruchka_total = sum(u.vyruchka.values())
    return u


def _minimum(u, par, mes):
    """Минимум взноса в накопление (Р-А по сверке 17.08.2026).

    У Чартаева накопление — обязанность, а не остаток: перечислить меньше минимума
    было нельзя, а кто не дотягивал, тому урезали земельный пай. Наша шкала Р-7
    сняла обязанность целиком, и в бедный месяц накопление обнулялось. Возвращаем
    нижнюю границу, сохраняя шкалу сверху.

    Доплата не загоняет человека в минус: иначе он кладёт в свой пай и тут же берёт
    из общего фонда — оборот без смысла.
    """
    cfg = par.get("minimum_nakopleniya", {})
    if not cfg.get("vklyuchen"):
        return
    deti = set(mes.get("deti", []))
    kto_pod = [k for k in u.uchastniki
               if cfg.get("primenyaetsya_k") != "vzroslye" or k not in deti]
    doli = par["nakoplenie"]
    vsego_doli = sum(doli.values())
    if vsego_doli <= 1e-12:
        # Субсчетам накопления заданы нулевые доли — доплате физически некуда
        # идти. Раньше здесь стояло `or 1.0`: потребление списывалось, а в фонды
        # не попадало ничего, и деньги исчезали молча.
        u.preduprezhdeniya.append(
            "минимум взноса не применён: все доли субсчетов накопления нулевые — "
            "доплате некуда идти")
        return

    for kto in kto_pod:
        if cfg.get("tip") != "dolya_vyruchki":
            continue
        trebuetsya = u.vyruchka[kto] * cfg.get("znachenie", 0.0)
        fakticheski = u.vyruchka[kto] * u.norma
        nedobor = trebuetsya - fakticheski
        if nedobor <= 1e-9:
            continue
        u.min_trebuetsya[kto] = trebuetsya
        dostupno = nedobor
        if cfg.get("ne_glubzhe_nulya"):
            dostupno = min(nedobor, max(0.0, _lichnyy(u, kto)))
        if dostupno > 1e-9:
            u.potreblenie[kto] -= dostupno          # из своего кармана в свои субсчета
            for f, dolya in doli.items():
                u.fondy[f] += dostupno * dolya / vsego_doli
            u.min_doplata[kto] += dostupno
        ostalos = nedobor - dostupno
        if ostalos > 1e-9:
            u.min_nedobor[kto] += ostalos

    # Санкция: срезаем земельные дивиденды в той же доле, в какой не выполнен
    # минимум. Срезанное делится между выполнившими — пай урезан в их пользу.
    if u.min_nedobor and cfg.get("sankciya") == "urezat_zemelnye_dividendy":
        srezano = 0.0
        for kto, ned in u.min_nedobor.items():
            treb = u.min_trebuetsya[kto]
            if treb <= 1e-12:
                continue          # требовалось ноль — недобора быть не может, санкции нет
            dolya = min(1.0, ned / treb)
            s = u.dividendy[kto] * dolya
            if s <= 1e-9:              # срезать нечего: отрицательная санкция
                continue               # ПРИБАВЛЯЛА бы получателю
            u.dividendy[kto] -= s
            u.sankciya[kto] = s
            srezano += s
        vypolnili = [k for k in u.uchastniki if k not in u.min_nedobor]
        if srezano > 1e-9:
            if vypolnili:
                for k in vypolnili:
                    u.dividendy[k] += srezano / len(vypolnili)
            else:
                # Минимум не выполнил НИКТО — передавать срезанное некому.
                # Раньше здесь деньги исчезали: снимались со всех и не доставались
                # никому, сходимость расходилась ровно на срезанное (проверено:
                # 1 000.00 при двух взрослых и входящем фонде земельных паёв 1 000).
                # Возвращаем в фонд: наказание сохраняется — в этом месяце
                # дивидендов нет, — а деньги остаются в семье и раздадутся
                # в следующем месяце по праву членства.
                u.fondy["zemelnye_pai"] += srezano
                # Это возврат уже учтённых денег, а не новая выручка. Без пометки
                # «накопление за месяц» на экране выросло бы на срезанное.
                u.mezhdu_fondami += srezano
                u.preduprezhdeniya.append(
                    f"минимум взноса не выполнил никто: срезанные дивиденды "
                    f"{srezano:.2f} возвращены в фонд земельных паёв и будут "
                    f"розданы в следующем месяце")


def _lichnyy(u, kto):
    return (u.potreblenie[kto] - u.zatraty[kto] + u.dividendy[kto]
            + u.upravlenie[kto] + u.dohod_na_pai[kto] + u.pokrytie_komu[kto])


def _deficit(u, par, mes):
    """Дефицит: не хватило на обязательное. Решения владельца 17.08.2026 —
    заём с возвратом, три месяца подряд, режем пропорционально.

    Считается ПОСЛЕ всех начислений и затрат: раньше просто не видно, у кого
    минус. Порядок покрытия задан параметрами, имущественные паи не трогаются
    ни при каких условиях.
    """
    cfg = par.get("deficit", {})
    if not cfg.get("vklyuchen"):
        return

    minusy = {k: -_lichnyy(u, k) for k in u.uchastniki if _lichnyy(u, k) < -1e-9}
    # Хороший месяц: гасим долг из инвестиционного фонда, пока он не разнесён в паи.
    if not minusy:
        u.mes_deficita = 0
        if u.dolg > 1e-9:
            dostupno = u.fondy["investicii"] * cfg.get("vozvrat_dolya_iz_investiciy", 0.0)
            pogasit = min(u.dolg, dostupno)
            if pogasit > 0:
                u.fondy["investicii"] -= pogasit
                u.fondy["rezerv"] += pogasit
                # Это перекладывание внутри накопления, а не новые деньги из
                # выручки. Без отдельного счёта «накопление за месяц» на экране
                # завышалось ровно на погашенный долг.
                u.mezhdu_fondami += pogasit
                u.dolg -= pogasit
                u.pogasheno = pogasit
        return

    u.deficit = sum(minusy.values())
    u.mes_deficita += 1
    ostatok = u.deficit

    for fond in cfg.get("poryadok", []):
        if ostatok <= 1e-9:
            break
        dostupno = max(0.0, u.fondy[fond])
        if fond == "strahovoy":
            dostupno *= cfg.get("predel_strahovogo_v_mesyac", 1.0)
        vzyat = min(ostatok, dostupno)
        if vzyat <= 1e-9:
            continue
        u.fondy[fond] -= vzyat
        u.pokryto[fond] += vzyat
        if fond in cfg.get("vozvratnye", []):
            u.dolg += vzyat            # заём: вернём из накопления хорошего месяца
        # Раздаём пропорционально глубине минуса у каждого.
        for kto, m in minusy.items():
            u.pokrytie_komu[kto] += vzyat * m / u.deficit
        ostatok -= vzyat

    u.nepokryto = max(0.0, ostatok)
    predel = cfg.get("podryad_mesyacev_do_ostanovki")
    if u.nepokryto > 1e-9 and predel and u.mes_deficita > predel:
        raise SystemExit(
            f"ОСТАНОВКА: дефицит {u.nepokryto:.2f} не покрыт, и это "
            f"{u.mes_deficita}-й месяц подряд при пределе {predel}. "
            f"Расчёт дальше не идёт — нужно решение человека: сократить обязательные "
            f"затраты, разместить или продать актив, изменить состав участников. "
            f"Имущественные паи ({sum(u.imushch_pai.values()):.2f}) трогать нельзя по правилу.")


def pechat(u, zagolovok):
    par = u.par
    val = par.get("valyuta", "")
    print("\n" + "=" * 87)
    print(zagolovok)
    print(f"прожиточный минимум месяца {u.prozh_minimum:.2f} "
          f"(режим «{par.get('prozhitochnyy_minimum',{}).get('rezhim','фикс')}»)")
    print(f"внешний доход семьи {u.vneshniy_pritok:.0f} → норма накопления "
          f"{u.norma*100:.1f} % (потребление {(1-u.norma)*100:.1f} %)")
    print("=" * 87)
    print(f"{'участник':<8}{'выручка':>10}{'потребл.':>10}{'матзатр.':>10}"
          f"{'дивид.':>9}{'управл.':>9}{'помощь':>9}{'ЛИЧНЫЙ':>10}{'имущ.пай':>10}")
    itog = defaultdict(float)
    for kto in u.uchastniki:
        lich = _lichnyy(u, kto)
        print(f"{kto:<8}{u.vyruchka[kto]:>10.2f}{u.potreblenie[kto]:>10.2f}"
              f"{u.zatraty[kto]:>10.2f}{u.dividendy[kto]:>9.2f}{u.upravlenie[kto]:>9.2f}"
              f"{u.pokrytie_komu[kto]:>9.2f}{lich:>10.2f}{u.imushch_pai[kto]:>10.2f}")
        itog["vyr"] += u.vyruchka[kto]; itog["lich"] += lich; itog["pai"] += u.imushch_pai[kto]
    print("-" * 87)
    print(f"{'итого':<8}{itog['vyr']:>10.2f}{'':>10}{'':>10}{'':>9}{'':>9}{'':>9}"
          f"{itog['lich']:>10.2f}{itog['pai']:>10.2f}")

    if u.min_doplata or u.min_nedobor:
        cfg = par["minimum_nakopleniya"]
        print(f"\nМинимум взноса в накопление ({cfg['znachenie']*100:.0f} % выручки, "
              f"{cfg['primenyaetsya_k']})")
        for kto in u.uchastniki:
            if u.min_doplata[kto] or u.min_nedobor[kto]:
                hvost = ""
                if u.min_nedobor[kto]:
                    hvost = (f"   не добрал {u.min_nedobor[kto]:.2f}, "
                             f"срезано дивидендов {u.sankciya[kto]:.2f}")
                print(f"  {kto:<8}требовалось {u.min_trebuetsya[kto]:>8.2f}"
                      f"   доплатил {u.min_doplata[kto]:>8.2f}{hvost}")

    pm = u.prozh_minimum
    if u.deficit or u.dolg or u.pogasheno:
        print(f"\nДефицит и заём (прожиточный минимум {pm:.0f})")
        if u.deficit:
            print(f"  не хватило на обязательное      {u.deficit:>10.2f}"
                  f"   ({u.mes_deficita}-й месяц подряд)")
            for f, v in u.pokryto.items():
                vozvr = " (заём, вернуть)" if f in par["deficit"].get("vozvratnye", []) else ""
                print(f"  покрыто из «{f}»{'':<8}{v:>10.2f}{vozvr}")
            if u.nepokryto:
                print(f"  НЕ ПОКРЫТО, легло на людей      {u.nepokryto:>10.2f}   ← режется пропорционально")
        if u.pogasheno:
            print(f"  погашено долга из инвестиций    {u.pogasheno:>10.2f}")
        print(f"  долг резервному фонду: было {u.dolg_vhod:.2f} → стало {u.dolg:.2f}")

    print(f"\nОстатки фондов, {val}")
    for f, v in u.fondy.items():
        if abs(v) > 1e-9:
            print(f"  {f:<18}{v:>10.2f}" + ("   ← минус!" if v < -1e-9 else ""))

    if u.karmany:
        print(f"\nКарманы (копится на нерегулярное), {val}")
        for k, v in u.karmany.items():
            print(f"  {k:<24}{v:>10.2f}" + ("   ← ушёл в минус, покупка не по карману" if v < -1e-9 else ""))

    if u.kapital_dohod:
        print(f"\nДоход на капитал за месяц: {u.kapital_dohod:.2f} {val}"
              f"  (доля управления {u.kapital_upravlenie:.2f}, "
              f"на паи {sum(u.dohod_na_pai.values()):.2f}, "
              f"в фонды {sum(u.dohod_fondam.values()):.2f})")

    if u.preduprezhdeniya:
        print("\nПредупреждения")
        for w in u.preduprezhdeniya:
            print("  ! " + w)

    print(f"\nДеньги: извне пришло {u.vneshniy_pritok:.2f}, "
          f"внутреннего оборота {u.vnutrenniy_oborot:.2f}, "
          f"суммарная «выручка семьи» {itog['vyr']:.2f}"
          f"  ← оборот раздувает «доход семьи» на {itog['vyr'] - u.vneshniy_pritok:.2f}")
    vhod = sum(u.pai_vhod.values()) + sum(u.fondy_vhod.values())
    svod = (itog['lich'] + itog['pai'] + sum(u.fondy.values())
            + sum(u.zatraty.values()) + u.vneshniy_rashod - vhod)
    if abs(svod - u.vneshniy_pritok) >= 1e-6:
        raise SystemExit(
            f"Сходимость нарушена: {svod:.2f} ≠ {u.vneshniy_pritok:.2f} — "
            f"деньги создались или исчезли, расчёт остановлен")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    par_p = args[0] if args else os.path.join(HERE, "parametry.json")
    mes_p = args[1] if len(args) > 1 else os.path.join(HERE, "mesyac.json")
    sravnit = "--sravnit" in sys.argv
    if sravnit:
        sys.argv.remove("--sravnit")
    par, mes = zagruzit(par_p, mes_p)
    pechat(schitat(par, mes), "РАСЧЁТ МЕСЯЦА")
    if sravnit:                      # отклонённый вариант — только по прямой просьбе
        pechat(schitat(par, mes, True),
               "ДЛЯ СРАВНЕНИЯ — коммуналка из фонда накопления (отклонено 16.08.2026)")
