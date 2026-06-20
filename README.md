# Cal_E3632A – Keysight E3632A automatikus kalibrációs program

> **⚠ Kísérleti állapot:** A program jelenleg kísérleti fázisban van, éles körülmények között
> nem tesztelt. Valós kalibrációs műveletek elvégzése előtt az eredményeket manuálisan
> ellenőrizni kell.

Keysight E3632A DC tápegység automatikus kalibrációs GUI-ja. 4 önálló kalibrációs blokk
futtatható szabadon, tetszőleges sorrendben. A tápegység RS-232 null-modem kábelen, a
referencia multiméter TCP socketen csatlakozik.

## Kalibrációs blokkok

| Blokk | Leírás | Bekötés |
|-------|--------|---------|
| Blokk A | DAC DNL korrekció + Feszültség kalibráció | DMM a PSU kimenetein, terhelés nélkül |
| Blokk B | OVP (túlfeszültség védelem) kalibráció | azonos, kimenet nyitott |
| Blokk C | Áram kalibráció (I LO / I MI / I HI) | 0,01 Ω sönt a kimeneteken, DMM a sönt végein |
| Blokk D | OCP (túláram védelem) kalibráció | sönt marad (rövidzár!) |

## Kapcsolódás

| Eszköz | Kapcsolat |
|--------|-----------|
| Keysight E3632A PSU | RS-232 null-modem kábel (9600 baud, 8N2, DTR/DSR) |
| Keysight 34465A DMM | TCP SCPI Sockets, port 5025 |

A program a Keysight 34465A multiméterrel kommunikál — a DMM vezérlőosztálya a
**[DMM_34465A](https://github.com/kvez/DMM_34465A)** projektből származik.

## Követelmények

```
pip install pyserial
```

## Futtatás

```bat
python cal_e3632a.py
```

## Build (önálló exe)

```bat
build.bat
```

Kimenet: `dist\Cal_E3632A.exe`

## Forrás

Keysight E3632A Service Guide (9018-01459) – Table 1-4, 43–58. oldal
