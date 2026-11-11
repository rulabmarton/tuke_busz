# Tüke Busz (Pécs) – Home Assistant integráció

Pécsi helyi buszmenetrend Home Assistantban, a Tüke Busz Zrt. hivatalos GTFS menetrendje alapján
(`https://mobilitas.biokom.hu/gtfs/`).

- **Következő járatok A → B**: a beállított megállóból induló következő *N* busz, amely a cél megállóba is eljut.
  Az integráció kiírja a pontos indulási és érkezési dátumot és időt, valamint a hátralévő időt („5 perc múlva”).
- **Menetrend nézet**: megmutatja, hogy
  - egy járat mikor indul az egyes megállókból egy időintervallumon belül, vagy
  - egy megállóból mikor indulnak buszok egész nap (vagy egy időintervallumban).
- **Saját kártya** (`custom:tukebusz-card`): az integráció automatikusan regisztrálja, nem kell külön erőforrásként hozzáadni.

> Az adatok **menetrend szerintiek**, nem valós idejűek. A Tüke Busz nem ad ki nyilvános valós idejű adatot.

## Telepítés

**Marketplace (HA 2026.11+)**: a Beállítások → Marketplace oldalon add hozzá ezt a GitHub repót egyéni (custom)
repositoryként, típusa *Integration*. Telepítsd, majd add hozzá az integrációt.

**Kézzel**: másold a `custom_components/tukebusz` mappát a HA `config/custom_components/` mappájába, és indítsd újra a HA-t.

Ezután: Beállítások → Eszközök és szolgáltatások → Integráció hozzáadása → **Tüke Busz (Pécs)**.

## Beállítás

Minden útvonal (A → B) külön bejegyzés.

| Mező | Leírás |
|---|---|
| Honnan | Indulási megálló. A megállónév az összes kocsiállást jelenti, a „Név – kocsiállás” elem csak egyet. |
| Hová | Cél megálló (opcionális). Üresen hagyva a megálló összes indulását mutatja. |
| Indulások száma | Hány következő járatot jelenítsen meg (1–20). |
| Frissítési gyakoriság | Milyen gyakran számolja újra a következő járatokat (másodperc, alapértelmezés: 60). |
| Járatszűrő | Pl. `3, 30Y`. Üresen hagyva minden járatot mutat. |
| Menetrend letöltése | Hány óránként töltse le újra a GTFS menetrendet (alapértelmezés: 24). |

A beállítások később a bejegyzés *Konfigurálás* gombjával módosíthatók.

### Entitások

- `sensor.tuke_busz_<honnan>_<hova>`: az állapot a következő indulás időpontja (timestamp).
  Attribútumok:
  - `departures`: lista, elemenként `line`, `headsign`, `departure`, `departure_str`, `arrival`, `arrival_str`,
    `minutes_until`, `relative`, `duration_min`, `origin_platform`, `color` stb.
  - `origin`, `destination`, `generated_at`
- `sensor.tuke_busz_<…>_menetrend_letoltve` (diagnosztikai): a menetrend utolsó letöltésének ideje és érvényességi ideje.

## Kártya

Következő járatok:

```yaml
type: custom:tukebusz-card
mode: departures
entity: sensor.tuke_busz_fopalyaudvar_kertvaros
# title: Munkába
# max: 5               # legfeljebb ennyi sor
# show_arrival: false  # érkezési idő elrejtése
```

Interaktív menetrend (megálló / járat választó, dátum, időintervallum vagy egész nap):

```yaml
type: custom:tukebusz-card
mode: timetable
view: stop          # stop | line
stop: Főpályaudvar  # opcionális előbeállítás (megállónév vagy stop_id)
# line: "15"
# start: "06:00"    # start/end nélkül egész nap
# end: "09:00"
```

## Szolgáltatások (actions)

A kártya ezeket használja, de automatizációkban is hívhatók (válaszadó szolgáltatások):

```yaml
action: tukebusz.get_timetable
data:
  mode: line        # vagy: stop
  line: "15"
  start: "16:00"
  end: "18:00"
response_variable: menetrend
```

- `tukebusz.get_journeys`: következő járatok A → B
- `tukebusz.get_timetable`: megálló- vagy járatmenetrend
- `tukebusz.get_metadata`: megállók és járatok listája
- `tukebusz.refresh_feed`: menetrend azonnali letöltése

## Fejlesztés

```bash
python -m unittest discover tests        # a GTFS mag tesztjei (HA nélkül futnak)
python scripts/smoke_real_feed.py Főpályaudvar Kertváros   # futtatás a valódi menetrenddel
```

A GTFS-feldolgozás (`custom_components/tukebusz/gtfs.py`) nem függ a Home Assistanttól.

---

### English summary

This custom integration provides Tüke Busz (Pécs, Hungary) **scheduled** departures from the official GTFS feed:
- A→B "next N journeys" sensors, with exact date/time and relative time
- `tukebusz.get_timetable` / `get_journeys` / `get_metadata` response actions
- a bundled Lovelace card, `custom:tukebusz-card`, with `departures` and interactive `timetable` modes

Install it via Settings → Marketplace (custom repository) or by copying `custom_components/tukebusz`.
Requires Home Assistant 2024.12 or newer.
