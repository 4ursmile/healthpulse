# Case study: the 2023-24 dengue season in Brazil, replayed

Brazil's 2023-24 season was the largest on record: 6,700,498 probable cases across the 26 states
in the IMDC data (ES excluded). This replay asks one question: **how many weeks earlier would each state have been warned
that it was heading past the epidemic level, compared with threshold monitoring?**

Reproduce with `hp sprint` (downloads the official IMDC files) and then `hp retro`. Output: `data/sprint/retro_2023_24.json`,
API `/api/v1/sprint/retro`. Code: `healthpulse/sprint/retro.py`.

## Set-up

- **Event.** The Ministry of Health epidemic level: cumulative season incidence above 300 probable cases per 100k
  (season = EW41 to EW40). No emergency-decree dates are used.
- **Status quo.** Threshold monitoring on reported data. The crossing is seen when that week's data arrives, assumed to be
  2 weeks after onset.
- **HealthPulse.** Each week it sees the data up to 2 weeks ago. It projects the unseen weeks
  plus the next 4 with a damped growth trend from the last 3 visible weeks, and alerts when
  the projected cumulative incidence passes 300/100k.
- **Fair comparator.** The same rule with the last visible week carried forward flat, so the effect of the growth model can
  be separated from the effect of simply projecting forward.
- **False alarms.** The identical rule (not tuned on 2023-24) is replayed over 2015-2022.

## Results

| | 2023-24 | 2015-2022 |
|---|---|---|
| State-seasons past the epidemic level | 19 of 26 | 85 of 208 |
| Warned before the status quo | **19 of 19** | 85 of 85 |
| Median weeks gained (HealthPulse) | **6** | 7 |
| Median weeks gained (flat carry-forward) | 4 | 4 |
| Same, epidemics that crossed before their peak | 5 vs 3 flat (13 states) | 6 vs 4 flat (51) |
| False alarms (alert, never crossed) | 1 of 7 (flat 0) | 9 of 123 = 7% (flat 1) |

**What this means.** Projecting forward instead of waiting for the data gains about 4
weeks on its own. The growth model adds about 2 more weeks for epidemics that were still rising. It costs more false alarms:
9 against 1 over 2015-2022. Choosing between the two is a policy decision
about how much an unnecessary mobilisation costs compared with a late one.

### Per state, 2023-24 (dates are week starts)

| State | Incidence /100k | Crossed 300 | Status quo sees it | HealthPulse alert | Weeks gained | Flat rule | Peak week |
|---|---|---|---|---|---|---|---|
| AC | 1,106 | 2023-12-10 | 2023-12-24 | 2023-11-19 | **5** | 3 | 2023-12-10 |
| DF | 9,844 | 2023-12-17 | 2023-12-31 | 2023-12-03 | **4** | 4 | 2024-02-18 |
| MG | 8,350 | 2024-01-07 | 2024-01-21 | 2023-12-10 | **6** | 4 | 2024-03-17 |
| GO | 4,905 | 2024-01-14 | 2024-01-28 | 2024-01-14 | **2** | 2 | 2024-03-10 |
| PR | 5,691 | 2024-01-14 | 2024-01-28 | 2023-12-24 | **5** | 2 | 2024-03-31 |
| RJ | 1,866 | 2024-02-04 | 2024-02-18 | 2024-01-14 | **5** | 4 | 2024-02-18 |
| AP | 1,380 | 2024-02-11 | 2024-02-25 | 2024-01-14 | **6** | 2 | 2024-02-25 |
| SC | 4,560 | 2024-02-11 | 2024-02-25 | 2024-02-04 | **3** | 2 | 2024-04-07 |
| SP | 4,777 | 2024-02-11 | 2024-02-25 | 2024-01-14 | **6** | 3 | 2024-05-05 |
| MT | 1,117 | 2024-02-18 | 2024-03-03 | 2024-01-14 | **7** | 4 | 2024-04-28 |
| BA | 1,613 | 2024-02-25 | 2024-03-10 | 2024-02-11 | **4** | 2 | 2024-03-31 |
| RS | 1,983 | 2024-03-03 | 2024-03-17 | 2024-02-18 | **4** | 3 | 2024-04-14 |
| MS | 675 | 2024-03-17 | 2024-03-31 | 2024-01-21 | **10** | 5 | 2024-03-10 |
| RN | 522 | 2024-04-07 | 2024-04-21 | 2024-03-03 | **7** | 5 | 2024-03-10 |
| PI | 440 | 2024-05-05 | 2024-05-19 | 2024-03-03 | **11** | 7 | 2024-03-10 |
| AL | 580 | 2024-05-26 | 2024-06-09 | 2024-03-03 | **14** | 6 | 2024-07-07 |
| TO | 328 | 2024-05-26 | 2024-06-09 | 2024-04-21 | **7** | 7 | 2024-04-14 |
| RO | 320 | 2024-06-02 | 2024-06-16 | 2024-03-03 | **15** | 15 | 2024-03-03 |
| PB | 359 | 2024-06-23 | 2024-07-07 | 2024-03-03 | **18** | 14 | 2024-03-17 |

Below the level: AM (236), CE (145), MA (154), PA (232), PE (284, false alarm 2024-03-03), RR (91), SE (127).

The leads of 10 weeks or more (MS, PI, AL, RO, PB) come from states that crossed 300/100k slowly, *after* their peak. The early
alert was right, but those weeks matter less operationally than the leads in states that crossed while still rising
(DF, MG, PR, SP, RJ and the others crossing before their peak).

### June outlook (IMDC season forecast made at EW25 2023)

The season model forecast 17 states above their climatological normal, and all
17 of those were above normal; 24 states ended above normal. It **badly
under-called the size**: a national median of 902,114 cases against a climatology of
534,949 and an observed 6,700,498. A season-ahead model can point the
direction in June; it cannot foresee a record wave. The weekly warning above is where the operational value is.

## Caveats

- Final-vintage counts replayed with a fixed reporting delay; real-time counts are lower and revised (PIT_UNVERIFIED).
- The event is the incidence threshold, not an official emergency decree; decree dates were not used.
- The alert rule was not tuned on 2023-24; the same rule and settings are replayed over 2015-2022 for false alarms.
- A projection crossing the threshold is a warning for planners, not a forecast of hospital load.
