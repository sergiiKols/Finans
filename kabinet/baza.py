#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""База кабинета: журнал фактов и состав семьи.

Главное правило: в базе лежат ФАКТЫ, а не посчитанные числа. Кто, когда, за что,
сколько. Все доли, паи и фонды считаются заново ядром при каждом открытии отчёта.
Поэтому изменение правила (норматив, минимум, тариф) не портит накопленное —
пересчитали и всё.
"""
import os
import sqlite3
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "uchet.db")

SHEMA = """
CREATE TABLE IF NOT EXISTS uchastniki(
  id       INTEGER PRIMARY KEY,
  imya     TEXT NOT NULL,
  vozrast  INTEGER,
  rodstvo  TEXT,
  otdelno  INTEGER DEFAULT 0,      -- живёт отдельно от семьи
  roditeli TEXT DEFAULT '',        -- имена родителей через запятую (для сына/дочери)
  hozyaystvo INTEGER DEFAULT 0,    -- ведёт хозяйство: получатель доли управления
  svyaz_s  TEXT DEFAULT '',        -- чей родитель (для бабушки/дедушки)
  aktiven  INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS operacii(
  id          INTEGER PRIMARY KEY,
  data        TEXT NOT NULL,       -- YYYY-MM-DD
  tip         TEXT NOT NULL,       -- vneshnyaya | vnutrennyaya | iz_fonda | zatrata
  uchastnik   TEXT,                -- кому пришло (для затрат пусто)
  summa       REAL NOT NULL,
  statya      TEXT,                -- за что / статья затрат
  istochnik   TEXT,                -- от кого: участник или фонд
  kommentariy TEXT DEFAULT '',
  sozdal      TEXT DEFAULT '',     -- кто внёс запись
  sozdano     TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_op_data ON operacii(data);
CREATE TABLE IF NOT EXISTS nastroyki(klyuch TEXT PRIMARY KEY, znachenie TEXT);
CREATE TABLE IF NOT EXISTS scheta(
  id        INTEGER PRIMARY KEY,
  nazvanie  TEXT NOT NULL,
  vid       TEXT NOT NULL,          -- tekushchiy | nakopitelnyy | nalichnye | prochee
  ostatok   REAL NOT NULL DEFAULT 0,
  stavka    REAL NOT NULL DEFAULT 0, -- годовых, доля
  nakoplenie INTEGER DEFAULT 0,      -- считается ли этот счёт хранилищем накоплений
  obnovlen  TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS perevody(
  id        INTEGER PRIMARY KEY,
  data      TEXT NOT NULL,        -- YYYY-MM-DD, день перевода
  summa     REAL NOT NULL,
  so_scheta INTEGER,              -- id счёта-источника (может быть пусто)
  na_schet  INTEGER,              -- id накопительного счёта
  kommentariy TEXT DEFAULT '',
  sozdal    TEXT DEFAULT '',
  sozdano   TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_per_data ON perevody(data);
CREATE TABLE IF NOT EXISTS plan(
  mesyac TEXT NOT NULL,          -- YYYY-MM
  vid    TEXT NOT NULL,          -- postuplenie | zatrata
  imya   TEXT NOT NULL,          -- участник или статья затрат
  summa  REAL NOT NULL,
  PRIMARY KEY(mesyac, vid, imya)
);
"""


def soedinit():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SHEMA)
    # База могла быть заведена до появления колонки — доращиваем на месте,
    # чтобы обновление кабинета не требовало от человека ничего.
    est = {r[1] for r in con.execute("PRAGMA table_info(uchastniki)")}
    if "svyaz_s" not in est:
        con.execute("ALTER TABLE uchastniki ADD COLUMN svyaz_s TEXT DEFAULT ''")
    if "hozyaystvo" not in est:
        con.execute("ALTER TABLE uchastniki ADD COLUMN hozyaystvo INTEGER DEFAULT 0")
    con.commit()
    return con


def rezervnaya_kopiya():
    """Копия базы при каждом запуске. Домашний диск — не надёжное место."""
    if not os.path.exists(DB):
        return None
    papka = os.path.join(HERE, "kopii")
    os.makedirs(papka, exist_ok=True)
    imya = os.path.join(papka, "uchet-%s.db" % datetime.now().strftime("%Y%m%d"))
    if not os.path.exists(imya):
        with open(DB, "rb") as src, open(imya, "wb") as dst:
            dst.write(src.read())
        # Держим последние 30 копий, дальше старые удаляются.
        kopii = sorted(f for f in os.listdir(papka) if f.endswith(".db"))
        for f in kopii[:-30]:
            os.remove(os.path.join(papka, f))
    return imya


def sostav(con):
    return [dict(r) for r in con.execute(
        "SELECT * FROM uchastniki WHERE aktiven=1 ORDER BY id")]


def operacii_mesyaca(con, god_mesyac):
    return [dict(r) for r in con.execute(
        "SELECT * FROM operacii WHERE data LIKE ? ORDER BY data, id",
        (god_mesyac + "-%",))]


def mesyacy(con):
    return [r[0] for r in con.execute(
        "SELECT DISTINCT substr(data,1,7) m FROM operacii ORDER BY m DESC")]


def dobavit_operaciyu(con, o):
    con.execute(
        "INSERT INTO operacii(data,tip,uchastnik,summa,statya,istochnik,"
        "kommentariy,sozdal,sozdano) VALUES(?,?,?,?,?,?,?,?,?)",
        (o["data"], o["tip"], o.get("uchastnik", ""), float(o["summa"]),
         o.get("statya", ""), o.get("istochnik", ""), o.get("kommentariy", ""),
         o.get("sozdal", ""), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()
    return con.execute("SELECT last_insert_rowid()").fetchone()[0]


def udalit_operaciyu(con, oid):
    con.execute("DELETE FROM operacii WHERE id=?", (oid,))
    con.commit()


def sohranit_sostav(con, lyudi):
    # DELETE без транзакции: если INSERT упадёт на середине — таблица останется
    # пустой, а повторный вызов с тем же списком даст другой результат (часть
    # имён уже в базе). Теперь обёрнуто в BEGIN, с rollback при любой ошибке.
    try:
        con.execute("DELETE FROM uchastniki")
        for p in lyudi:
            con.execute(
                "INSERT INTO uchastniki(imya,vozrast,rodstvo,otdelno,roditeli,svyaz_s,"
                "hozyaystvo,aktiven) VALUES(?,?,?,?,?,?,?,1)",
                (p["imya"], int(p.get("vozrast") or 0), p.get("rodstvo", ""),
                 1 if p.get("otdelno") else 0, ",".join(p.get("roditeli", [])),
                 ",".join(p.get("svyaz_s", [])), 1 if p.get("hozyaystvo") else 0))
        con.commit()
    except Exception:
        con.rollback()
        raise


def nastroyka(con, klyuch, znachenie=None):
    if znachenie is None:
        r = con.execute("SELECT znachenie FROM nastroyki WHERE klyuch=?", (klyuch,)).fetchone()
        return r[0] if r else None
    con.execute("INSERT OR REPLACE INTO nastroyki VALUES(?,?)", (klyuch, znachenie))
    con.commit()


def plan_mesyaca(con, gm):
    """План месяца: что ожидаем получить и что обязаны потратить.

    Нужен до первого поступления: норма накопления у нас прогрессивная, доля
    первой недели зависит от того, что придёт в четвёртую. Без плана делить нечем.
    """
    r = {"postupleniya": {}, "zatraty": {}}
    for x in con.execute("SELECT vid,imya,summa FROM plan WHERE mesyac=?", (gm,)):
        r["postupleniya" if x["vid"] == "postuplenie" else "zatraty"][x["imya"]] = x["summa"]
    return r


def sohranit_plan(con, gm, postupleniya, zatraty):
    con.execute("DELETE FROM plan WHERE mesyac=?", (gm,))
    for vid, d in (("postuplenie", postupleniya), ("zatrata", zatraty)):
        for imya, summa in d.items():
            if str(imya).strip() and float(summa or 0) > 0:
                con.execute("INSERT INTO plan VALUES(?,?,?,?)",
                            (gm, vid, str(imya).strip(), float(summa)))
    con.commit()


def skopirovat_plan(con, iz_gm, v_gm):
    p = plan_mesyaca(con, iz_gm)
    sohranit_plan(con, v_gm, p["postupleniya"], p["zatraty"])
    return p


def predydushchiy_plan(con, gm):
    """Ближайший прошлый месяц, для которого план заведён."""
    r = con.execute("SELECT mesyac FROM plan WHERE mesyac<? ORDER BY mesyac DESC LIMIT 1",
                    (gm,)).fetchone()
    return r[0] if r else None


def scheta(con):
    return [dict(r) for r in con.execute("SELECT * FROM scheta ORDER BY id")]


def sohranit_scheta(con, spisok):
    """Остатки вводит человек: автоматически их взять неоткуда — банковского API
    для физлиц нет. Дата обновления пишется сама, чтобы было видно, насколько
    свежие числа."""
    con.execute("DELETE FROM scheta")
    for s in spisok:
        if not str(s.get("nazvanie", "")).strip():
            continue
        con.execute(
            "INSERT INTO scheta(nazvanie,vid,ostatok,stavka,nakoplenie,obnovlen)"
            " VALUES(?,?,?,?,?,?)",
            (s["nazvanie"].strip(), s.get("vid", "prochee"),
             float(s.get("ostatok") or 0), float(s.get("stavka") or 0),
             1 if s.get("nakoplenie") else 0,
             datetime.now().strftime("%Y-%m-%d %H:%M")))
    con.commit()


def perevody_mesyaca(con, god_mesyac):
    return [dict(r) for r in con.execute(
        "SELECT * FROM perevody WHERE data LIKE ? ORDER BY data, id",
        (god_mesyac + "-%",))]


def posledniy_perevod(con, do_daty=None):
    """Последний отмеченный перевод — от него считается «за сколько дней копилось»."""
    if do_daty:
        r = con.execute("SELECT * FROM perevody WHERE data<=? ORDER BY data DESC, id DESC"
                        " LIMIT 1", (do_daty,)).fetchone()
    else:
        r = con.execute("SELECT * FROM perevody ORDER BY data DESC, id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def dobavit_perevod(con, p):
    """Отметка «перевёл»: факт перемещения денег между своими счетами.

    В журнал операций это НЕ пишется намеренно. Перевод не выручка и не затрата:
    деньги не покидают семью и в распределении не участвуют. Ядро о переводах не
    знает и знать не должно — иначе один и тот же рубль посчитается дважды.
    Остатки счетов двигаются здесь же: в банке они уже сдвинулись.
    """
    summa = float(p["summa"])
    if summa <= 0:
        raise ValueError("сумма перевода должна быть больше нуля")
    na = p.get("na_schet")
    so = p.get("so_scheta")
    con.execute(
        "INSERT INTO perevody(data,summa,so_scheta,na_schet,kommentariy,sozdal,sozdano)"
        " VALUES(?,?,?,?,?,?,?)",
        (p["data"], summa, int(so) if so else None, int(na) if na else None,
         p.get("kommentariy", ""), p.get("sozdal", ""),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    _dvinut_ostatki(con, so, na, summa)
    con.commit()
    return con.execute("SELECT last_insert_rowid()").fetchone()[0]


def udalit_perevod(con, pid):
    """Отмена отметки: остатки возвращаются, иначе счета разъедутся с банком."""
    r = con.execute("SELECT * FROM perevody WHERE id=?", (pid,)).fetchone()
    if not r:
        return False
    _dvinut_ostatki(con, r["na_schet"], r["so_scheta"], float(r["summa"]))
    con.execute("DELETE FROM perevody WHERE id=?", (pid,))
    con.commit()
    return True


def _dvinut_ostatki(con, so_scheta, na_schet, summa):
    """Снять с одного счёта, положить на другой. Пустой id — счёт не назван."""
    teper = datetime.now().strftime("%Y-%m-%d %H:%M")
    for sid, znak in ((so_scheta, -1), (na_schet, +1)):
        if sid:
            con.execute("UPDATE scheta SET ostatok=ROUND(ostatok+?,2), obnovlen=?"
                        " WHERE id=?", (znak * summa, teper, int(sid)))


def schet_nakopleniya(con):
    """Куда переводим. Первый счёт с отметкой «хранилище накоплений»."""
    r = con.execute("SELECT * FROM scheta WHERE nakoplenie=1 ORDER BY id LIMIT 1").fetchone()
    return dict(r) if r else None


def ochistit_plan(con, mesyac=None):
    if mesyac:
        con.execute("DELETE FROM plan WHERE mesyac=?", (mesyac,))
    else:
        con.execute("DELETE FROM plan")
    con.commit()
