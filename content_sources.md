# Where the seed data of the content engine comes from
> Code: [content.py](content.py): `SEED_NUMBERS`, `SEED_VIDEOS`, `SEED_PLACES`, `SEED_PLACES_CITIES`

The seed data goes into the database once (a mark in the `content_seeded` table); after that the admin edits it
(figma 16, 17). Numbers, videos and the Kabul places were checked on 06.11.2026, places outside Kabul on 07.11.2026.

## Emergency numbers
| number | Dari / Pashto | basis |
|---|---|---|
| 119 | پولیس / پولیس | The group (06.11). UK FCDO, [getting help](https://www.gov.uk/foreign-travel-advice/afghanistan/getting-help): "Emergency services in Afghanistan Telephone: 119 (police)". Canada, [travel.gc.ca](https://travel.gc.ca/destinations/afghanistan): "police: 119 or 100". |
| 102 | آمبولانس / امبولانس | The group (06.11). UK FCDO, [health](https://www.gov.uk/foreign-travel-advice/afghanistan/health): "The telephone number for the ambulance service in Kabul is 102 and each province has a separate emergency number." Canada: "medical assistance: 102". |
| 100 | پولیس (شمارهٔ دوم) / پولیس (دوهمه شمېره) | Canada, [travel.gc.ca](https://travel.gc.ca/destinations/afghanistan) (Last updated: 14 October 2026): "police: 119 or 100". US State Department, [Afghanistan](https://travel.state.gov/content/travel/en/international-travel/International-Travel-Country-Information-Pages/Afghanistan.html): "dial 119 or 100 for police" — the live page is blocked by Cloudflare (403), so the [archive.org copy of 01.04.2026](http://web.archive.org/web/20260401011325/https://travel.state.gov/content/travel/en/international-travel/International-Travel-Country-Information-Pages/Afghanistan.html) was read (the page says "Last Updated: May 31, 2024"). The telecom regulator ATRA via [ITU](https://www.itu.int/dms_pub/itu-t/oth/02/02/T02020000010003PDFE.pdf) (numbering plan of 28.08.2008): "Police 100". |
| ~~112~~ | ~~آتش‌نشانی / اور وژونکي~~ | **Not seeded since v3** (the group's request of 07.11: only an officially confirmed fire number). For: Canada "firefighters: 112"; US State Department (the same copy) "112 for fire". Against: ATRA (2008) lists firefighters as 101 and has no 112; the US Embassy in Kabul in 2013 gave 112 as the Kabul ambulance ([archive](https://webarchive.library.unt.edu/web/20130213211201/http://kabul.usembassy.gov/lmpa.html)); Australia Smartraveller: "There's no centralised number to reach emergency services". No Afghan source confirms it. |

All of these sources were opened and read on 06.11.2026: both UK FCDO pages, the Canada page, the archived US page
and the ITU document. Figma 16 had 119 "تلفون اضطراری", 112 "صلیب سرخ", 110 "پولیس": 110 is in none
of the sources, and 112 is nowhere named as the Red Cross or Red Crescent, so they are not used.

**Databases made before v3.** At the first start of v3 the 112 row is deleted once (mark `numbers_v3_drop_112` in
`content_seeded`), but only if it is exactly the v2 seed row (`112`, `آتش‌نشانی`, `اور وژونکي`, sort 4). A row the
admin edited, or a 112 the admin added later, stays.

## Training videos (figma 17)
Titles come from figma 17. A link is accepted only if https://www.youtube.com/oembed?url=…&format=json answered 200.

| topic (Dari) | link | channel (oEmbed author_name, author_url) | video title (oEmbed) | language, length |
|---|---|---|---|---|
| چگونه در هنگام سیلاب پناه بگیریم | [Glc-1f4Ez00](https://www.youtube.com/watch?v=Glc-1f4Ez00) | American Red Cross, [@redcross](https://www.youtube.com/@redcross) | Disaster Preparedness: Floods | English, 1:25 |
| چگونه زلزله را تشخیص دهیم | [MKILThtPxQs](https://www.youtube.com/watch?v=MKILThtPxQs) | FEMA, [@FEMA](https://www.youtube.com/@FEMA) | When The Earth Shakes - Animated Video | English (animation), 1:33 |
| کمک‌های اولیه در حوادث | [jrc0QEscOco](https://www.youtube.com/watch?v=jrc0QEscOco) | Afghan Red Crescent Society, [@ARCSAfghanistan](https://www.youtube.com/@ARCSAfghanistan) (arcs.af links to the channel) | آموزش کمک هاى اوليه؛ اقدامات لازم براى حالت #بيهوشى | Dari, 3:41 — first aid for loss of consciousness (a narrower topic than the card title) |
| آمادگی در برابر آتش‌سوزی | [tNYcJaeaRQg](https://www.youtube.com/watch?v=tNYcJaeaRQg) | American Red Cross, [@redcross](https://www.youtube.com/@redcross) | Home Fire Preparedness: Practice Makes Prepared – Build Your Home Fire Escape Plan | English, 2:39 |

The videos were found by searching the official channels listed below. oEmbed was checked again on 06.11.2026 at 01:43: all four returned 200, channels
and titles as in the table. Video lengths were read from the YouTube pages on 06.11.2026. In the database `source` = "organization,
language". There are no official videos in Dari or Pashto for topics 1, 2 and 4: the channels of UNICEF Afghanistan,
ARCS, IOM Afghanistan, IFRC, UNDRR, WHO, ICRC and UNHCR Afghanistan were checked.

## Safe places (examples)
Coordinates are the OpenStreetMap point from [Nominatim](https://nominatim.openstreetmap.org/) (the object's centre),
queried at most 1 request per second. These are **examples of open public places** in Kabul and, since v3, in eight
other cities (`source='example'`), not an official list of shelters.

### Kabul (queried 06.11.2026)

| place (Dari) | kind | lat, lon | OSM object | query |
|---|---|---|---|---|
| پارک شهر نو | park | 34.5331433, 69.1684384 | [way 25866557](https://www.openstreetmap.org/way/25866557) | "پارک شهر نو" |
| چمن حضوری | open_area | 34.5152174, 69.1951985 | [way 25866709](https://www.openstreetmap.org/way/25866709) | "چمن حضوری" |
| پارک زرنگار | park | 34.5212367, 69.1752644 | [way 201198534](https://www.openstreetmap.org/way/201198534) | "Zarnegar Park" |
| باغ بابر | park | 34.5026029, 69.1583798 | [way 199353387](https://www.openstreetmap.org/way/199353387) | "Babur Gardens" |
| باغ چهلستون | park | 34.4681673, 69.1523306 | [way 387447673](https://www.openstreetmap.org/way/387447673) | "پارک" |
| پارک حصه اول خیرخانه | park | 34.5743975, 69.1233809 | [way 713914709](https://www.openstreetmap.org/way/713914709) | "پارک خیرخانه" |
| ورزشگاه غازی | stadium | 34.5186050, 69.1939630 | [way 1494767421](https://www.openstreetmap.org/way/1494767421) | "Ghazi Stadium" |
| پوهنتون کابل | campus | 34.5154398, 69.1338400 | [way 199999770](https://www.openstreetmap.org/way/199999770) | "Kabul University" |
| پوهنتون پولی‌تخنیک کابل | campus | 34.5356621, 69.1199368 | [way 991169055](https://www.openstreetmap.org/way/991169055) | "Kabul Polytechnic University" |
| پوهنتون تعلیم و تربیه | campus | 34.5327049, 69.1122522 | [way 201345553](https://www.openstreetmap.org/way/201345553) | "Kabul Education University" |
| محوطهٔ قصر دارالامان | open_area | 34.4649317, 69.1199532 | [way 43633811](https://www.openstreetmap.org/way/43633811) | "Darul Aman Palace" |
| تپهٔ بی‌بی مهرو | hill | 34.5440046, 69.1830777 | [way 1349503002](https://www.openstreetmap.org/way/1349503002) | "Bibi Mahro" |

The address (district) comes from the Nominatim response (`accept-language=fa`).

### Other cities (v3, the group's request; queried 07.11.2026)
Query: `university`, `stadium` and `park`, each limited to a box of ±0.12° around the city centre (`bounded=1`,
`countrycodes=af`). Only objects with a name in OSM were taken; unnamed stadiums (Mazar-i-Sharif, Jalalabad, Kunduz,
Khost) were skipped rather than given an invented name. Databases made before v3 get these places once at the first
start (mark `places_v3_cities`).

| place (Dari) | kind | lat, lon | OSM object | name in OSM |
|---|---|---|---|---|
| پوهنتون هرات | campus | 34.3675322, 62.2111314 | [way 165334300](https://www.openstreetmap.org/way/165334300) | دانشگاه هرات / Herat University |
| ورزشگاه هرات | stadium | 34.3473974, 62.2176845 | [way 698630698](https://www.openstreetmap.org/way/698630698) | Herat Stadium |
| پوهنتون بلخ | campus | 36.7108014, 67.1275958 | [way 140170866](https://www.openstreetmap.org/way/140170866) | دانشگاه بلخ / Balkh University (Mazar-i-Sharif) |
| بوستان آریانا | park | 36.7118437, 67.1413598 | [way 931375434](https://www.openstreetmap.org/way/931375434) | بوستان آریانا (Mazar-i-Sharif) |
| پوهنتون ننگرهار | campus | 34.4747094, 70.3681243 | [way 89992226](https://www.openstreetmap.org/way/89992226) | Nangarhar University (Darunta, Jalalabad) |
| پارک جوی هفت | park | 34.4263639, 70.4305681 | [way 188315590](https://www.openstreetmap.org/way/188315590) | Joy Haft Parks (Jalalabad) |
| پوهنتون قندهار | campus | 31.6374143, 65.6971092 | [way 388657588](https://www.openstreetmap.org/way/388657588) | د کندهار پوهنتون (Arghandab, Kandahar) |
| ورزشگاه احمدشاهی | stadium | 31.6100534, 65.6883916 | [way 613382925](https://www.openstreetmap.org/way/613382925) | احمد شاهي لوبغالی (Kandahar) |
| پوهنتون قندوز | campus | 36.7112863, 68.8631860 | [relation 18234113](https://www.openstreetmap.org/relation/18234113) | پوهنتون قندوز / Kunduz University |
| پوهنتون فیض‌آباد | campus | 37.1038960, 70.5264848 | [way 805555796](https://www.openstreetmap.org/way/805555796) | دانشگاه فیض آباد (Faizabad, Badakhshan) |
| پوهنتون بامیان | campus | 34.8019149, 67.8338409 | [way 1567383434](https://www.openstreetmap.org/way/1567383434) | دانشگاه بامیان |
| پوهنتون خوست | campus | 33.3491318, 69.8687197 | [way 386935668](https://www.openstreetmap.org/way/386935668) | Khost University |

Dari names: where OSM uses the Iranian «دانشگاه», the app writes the Afghan Dari «پوهنتون», as for Kabul; English-only
OSM names (Herat Stadium, Nangarhar University, Joy Haft Parks, Khost University) and the Pashto-only ones (Kandahar)
were put into Dari for this project.

## Pashto
Pashto names from OSM (`name` or `name:ps`): د نوي ښار پارک, د زرنګار پارک, بابر بڼ, کابل پوهنتون, د هرات پوهنتون,
د کندهار پوهنتون, احمد شاهي لوبغالی, د کندوز پوهنتون; «د بامیان پوهنتون» is taken from the OSM name of its agriculture
faculty. For قصر دارالامان OSM gives "د دارال امن ماڼۍ", we use "د دارالامان ماڼۍ انګړ" (the palace courtyard). The
rest of the Pashto (numbers, videos, other places) is a translation made for this project: **a native speaker should
check it**.
