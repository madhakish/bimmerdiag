# DDE7 (N57/M57TU2) EDIABAS Reference

## Vehicle: E70 xDrive35d
## ECU: Bosch DDE7.3, SGBD: D73N57B0 / D73N57C0
## Confirmed ECU: BMW Part 8587807, Bosch, SW 55.84.78

---

## SGBD Detection

The group file `D_MOTOR` auto-detects the correct SGBD variant.
For this ECU it resolves to `D73N57C0`, but `D73N57B0` also works.

## Key Jobs

### Identification & Info
| Job | Purpose |
|-----|---------|
| `IDENT` | ECU identification (part number, SW version, manufacturer) |
| `INFO` | SGBD info (author, version, language) |
| `_JOBS` | List all 225 available jobs |
| `ECU_CONFIG` | Component configuration (what's coded on/off) |
| `SERIENNUMMER_LESEN` | Read ECU serial number |

### Fault Codes
| Job | Purpose |
|-----|---------|
| `FS_LESEN` | Read fault codes |
| `FS_LESEN_DETAIL` | Read fault code details (param: `0xCODE`) |
| `FS_LOESCHEN` | Clear ALL fault codes |
| `FS_SELEKTIV_LOESCHEN` | Clear specific fault code |
| `IS_LESEN` | Read info memory (shadow faults) |
| `IS_LESEN_DETAIL` | Read info memory details |

### Live Sensor Data
| Job | Result Name | Unit | Description |
|-----|-------------|------|-------------|
| `STATUS_MOTORDREHZAHL` | `STAT_MOTORDREHZAHL_WERT` | rpm | Engine RPM |
| `STATUS_UBATT` | `STAT_UBATT_WERT` | mV | Battery voltage |
| `STATUS_KUEHLMITTELTEMPERATUR` | `STAT_KUEHLMITTELTEMPERATUR_WERT` | degC | Coolant temp |
| `STATUS_MOTORTEMPERATUR` | `STAT_MOTORTEMPERATUR_WERT` | degC | Engine/oil temp |
| `STATUS_AN_LUFTTEMPERATUR` | `STAT_AN_LUFTTEMPERATUR_WERT` | degC | Intake air temp |
| `STATUS_ANSAUGLUFTTEMPERATUR` | `STAT_ANSAUGLUFTTEMPERATUR_WERT` | degC | Intake manifold temp |
| `STATUS_LADELUFTTEMPERATUR` | `STAT_LADELUFTTEMPERATUR_WERT` | degC | Charge air temp |
| `STATUS_UMGEBUNGSTEMPERATUR` | `STAT_UMGEBUNGSTEMPERATUR_WERT` | degC | Ambient temp |
| `STATUS_KRAFTSTOFFTEMPERATUR` | `STAT_KRAFTSTOFFTEMPERATUR_WERT` | degC | Fuel temp |
| `STATUS_LADEDRUCK_IST` | `STAT_LADEDRUCK_IST_WERT` | hPa | Boost pressure actual |
| `STATUS_LADEDRUCK_SOLL` | `STAT_LADEDRUCK_SOLL_WERT` | hPa | Boost pressure target |
| `STATUS_ATMOSPHAERENDRUCK` | `STAT_ATMOSPHAERENDRUCK_WERT` | hPa | Barometric pressure |
| `STATUS_LMM_MASSE` | `STAT_LMM_MASSE_WERT` | kg/h | MAF mass flow |
| `STATUS_LUFTMASSE_IST` | `STAT_LUFTMASSE_IST_WERT` | mg/hub | Air mass actual per stroke |
| `STATUS_LUFTMASSE_SOLL` | `STAT_LUFTMASSE_SOLL_WERT` | mg/hub | Air mass target per stroke |
| `STATUS_RAILDRUCK_IST` | `STAT_RAILDRUCK_IST_WERT` | bar | Common rail pressure actual |
| `STATUS_RAILDRUCK_SOLL` | `STAT_RAILDRUCK_SOLL_WERT` | bar | Common rail pressure target |
| `STATUS_KILOMETERSTAND` | `STAT_KILOMETERSTAND_WERT` | km | Odometer |
| `STATUS_OELNIVEAU` | `STATUS_OELNIVEAU` | % | Oil level (0-100) |
| `STATUS_BETRIEBSSTUNDENZAEHLER` | `STAT_BETRIEBSSTUNDENZAEHLER_WERT` | s | Operating hours |

### Injector Diagnostics
| Job | Purpose |
|-----|---------|
| `ABGLEICH_IMA_LESEN` | Read IMA injector matching codes (7-char codes per cylinder) |
| `STATUS_LAUFUNRUHE_DREHZAHL` | Idle roughness - RPM deviation per cylinder |
| `STATUS_LAUFUNRUHE_LLR_MENGE` | Idle roughness - fuel correction per cylinder (mg/stroke) |
| `STATUS_INJEKTORTAUSCH` | Injector usage counter status |
| `STATUS_OFFSETWERTE` | Injector offset values |
| `STATUS_OFFSETLERNEN` | Injector offset learning status |

### DPF / Exhaust
| Job | Purpose |
|-----|---------|
| `STATUS_PARTIKELFILTER_VERBAUT` | DPF installed status |
| `STATUS_REGENERATION_CSF` | DPF regeneration status |
| `STATUS_RESTLAUFSTRECKE_CSF` | DPF remaining distance |
| `STATUS_DIFFERENZDRUCK_CSF` | DPF differential pressure |
| `STATUS_ABGASTEMPERATUR_CSF` | Exhaust temp before DPF |
| `STATUS_ABGASTEMPERATUR_KAT` | Exhaust temp before cat |

### CBS (Condition Based Service)
| Job | Purpose |
|-----|---------|
| `CBS_DATEN_LESEN` | Read service data (oil life, mileage remaining) |
| `CBS_INFO` | CBS system info |
| `CBS_RESET` | Reset service interval |

### Measurement Blocks (advanced)
| Job | Purpose |
|-----|---------|
| `STATUS_MESSWERTBLOCK_LESEN` | Read arbitrary ECU internal variables |
| `FASTA_MESSWERTBLOCK_LESEN` | Fast measurement block read |

Usage: `STATUS_MESSWERTBLOCK_LESEN` with param `"JA;<variable_name>"`

Known variable names (from INPA scripts):
- `CoSCR_st` - SCR system status
- `CoSCR_stSub` - SCR sub-status
- `SCRAd_ctSlip` - SCR slip count
- `SCRAd_facPlaus_Fld_0` - SCR plausibility factor
- `SCRAd_facQtyAdap` - SCR quantity adaptation factor

## INPA Menu Translation

### Main Menu (Hauptmenu)
| Key | German | English |
|-----|--------|---------|
| F1 | Info | Info |
| F2 | Ident / SVK | Identify |
| F4 | Fehler | Faults (DTC) |
| F5 | Status | Status (live data) |
| F6 | Steuern | Control (actuator tests) |
| F7 | Speicher | Memory |
| F8 | Auswahl | Selection |
| F9 | Druck | Print |
| F10 | Ende | Exit |

### Sub-Menus
| German | English |
|--------|---------|
| Fehlerspeicher | Fault Memory |
| Status lesen | Read Status |
| Steuern | Control/Actuator Test |
| Speicher lesen | Read Memory |
| Systemchecks | System Checks |
| Analogwerte | Analog Values (live gauges) |
| Digitalwerte | Digital Values (on/off states) |
| Soll/Ist | Setpoint/Actual comparison |
| Glühkerzen (GLF) | Glow Plugs |
| EWS | Electronic Immobilizer |
| IMA | Injection Quantity Matching |
| SCR | AdBlue/DEF System |
| Ladeluftschlauch | Charge Air Hose |
| Luftmassenmesser | Mass Airflow Sensor |
| Kuehlmitteltemperatur | Coolant Temperature |
| Motordrehzahl | Engine RPM |
| Raildruck | Common Rail Pressure |
| Laufunruhe | Idle Roughness |

## ECU Config Flags (from ECU_CONFIG job)

| Flag | German | English |
|------|--------|---------|
| DPF | DPF-Status | Diesel Particulate Filter |
| DRO | Drosselklappen-Status | Throttle/Swirl Flap |
| GSG | GSG-Status | Glow Plug Controller |
| ACC | ACC-Status | Adaptive Cruise Control |
| MSA | MSA-Status | Auto Start-Stop |
| FGR | FGR-Status | Cruise Control |
| KLIMA | Klimaanlagen-Status | A/C System |
| PCSF | Drucksensor über Partikelfilter | DPF Pressure Sensor |
| TCSF | Abgastemperatur vor Partikelfilter | Exhaust Temp before DPF |
| TOXI | Abgastemperatursensor vor OxiCat | Exhaust Temp before OxiCat |
| PDIFF | Differenzdrucksensor | DPF Differential Pressure |
| ZUH | Zuheizer | Auxiliary Heater |
| KFH | Kraftstofffilterheizung | Fuel Filter Heater |
| BUS | Bremsunterdrucksensor | Brake Vacuum Sensor |
| MLA | Motorlager | Engine Mount (active) |
