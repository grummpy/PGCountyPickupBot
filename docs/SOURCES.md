# Sources

Checked **2026-10-06**. Collection days and holiday rules below are transcribed from public Prince George's County pages and from the county GIS services. They are not a substitute for the county's own lookup tool or PGC311.

The runtime program does not download the county website. Holiday shifts ship in `src/pgpickup/data/holidays.yaml`. Address lookup calls only the two ArcGIS REST endpoints below, at most once each per lookup, and then caches the result.

## What is machine-readable

| Source | Kind | Used for |
| --- | --- | --- |
| DoE `Trash_Services` MapServer, layer 2 | Public ArcGIS REST query endpoint | Trash, recycling, yard, and bulky day for a point |
| `DATA311/PGMD_Address_Locator` | Public ArcGIS geocode endpoint | Turn one address into a WGS84 point |
| County holiday and collection pages | HTML, not an API | Holiday-shift table and set-out rules, curated into YAML |

No separate open-data download (Socrata, CKAN, or a documented bulk dump) was found for collection days. The MapServer `copyrightText` and service item `licenseInfo` were empty on 2026-10-06. Treat the REST service as a public county query endpoint, not as a licensed open-data product.

## Official pages

### Holiday waste collection schedule

- URL: https://www.princegeorgescountymd.gov/departments-offices/environment/waste-recycling/holiday-waste-collection-schedule
- Kind: HTML page. A flyer is linked from the page; there is no JSON or iCal feed.
- Checked: 2026-10-06
- Rule stated at the top: on listed holidays, curbside bulky, trash, recycling, food scraps, and yard trim are not collected. If the holiday is a Monday, all collections, including organics, slide one day later in the week.
- No collection (slide): Martin Luther King, Jr. Day; Memorial Day; Juneteenth (observed); Independence Day (observed); Labor Day; Thanksgiving Day; Christmas Day (observed); New Year's Day (observed).
- Collection still runs: Day After Thanksgiving (County Employees' Appreciation Day); Native American Day; Veterans Day (observed); Presidents' Day.
- The page says a new holiday schedule took effect July 1, 2024.
- Municipal holiday schedules may differ. The page says so.
- White goods, appliances, scrap tires, electronics, and scrap metal are appointment-only through PGC311 and use a different schedule than curbside bulky trash.

The dated 2026 tables on that page are what `holidays.yaml` encodes. Where the heading and the table disagree, the table body was followed and the disagreement is noted in the YAML `note` field:

- Thanksgiving 2026 is Thursday, **November 26**. The heading says "Thursday, November 27, 2026". The table shifts Thursday, November 26 to Friday, November 27, and the Friday row (printed as 2025) to Saturday. Encoded as November 26 → November 27 and November 27 → November 28, 2026.
- Independence Day 2026 falls on Saturday, July 4. The dated table says Monday June 29 through Friday July 3 do **not** change. The intro list still names Independence Day (observed) as a no-collection holiday. This project follows the dated table, so Friday, July 3, 2026 does not slide.
- New Year's Day 2027 (Friday, January 1) is the only 2027 holiday on the page. Later 2027 dates are not invented.

### Residential trash collection

- URL: https://www.princegeorgescountymd.gov/departments-offices/environment/waste-recycling/trash-bulky-trash/trash-collection
- Kind: HTML page
- Checked: 2026-10-06
- Carts may go to the curb after 6:00 p.m. the day before collection. Collection runs between 6:00 a.m. and 8:00 p.m.
- Up to four acceptable curbside bulky items on the regular collection day.
- Electronics and scrap metal: appointment only, third Monday of each month, starting with a July 15, 2024 collection mentioned on the page. This bot does not create those appointments.
- Inclement-weather delays (policy dated November 2021 on the page) are announced by the county. They are not in the GIS layer or the holiday table.
- Contact printed on the page: Wayne K. Curry Administration Building, 1301 McCormick Drive, Largo, MD 20774. That public building is the sample address used to exercise the geocoder once. It is not a residence.

### Residential collections (yard trim, food scraps, bulky)

- URL: https://www.princegeorgescountymd.gov/departments-offices/environment/waste-recycling/residential-collections
- Kind: HTML page
- Checked: 2026-10-06
- Yard trim is collected weekly, and the page says **Monday only, year-round** (grass, leaves, small branches, brush, Christmas trees, and food scraps). Not accepted in plastic bags since January 1, 2014. Food scraps go out on Monday with organics.
- The GIS layer is slightly wider than that sentence: on 2026-10-06 `Yard_Day_of_Service` was Monday for 59 area combinations, Tuesday for 1, and "No Service" for 116. Lookup uses the layer value. The manual example config uses Monday, year-round.
- Curbside bulky: up to four items with regular trash, no appointment. Refrigerators, air conditioners, washers, dryers, and other appliances still need a PGC311 appointment, as do scrap tires.
- The same page also describes a temporary summer start of 5:00 a.m. from June 29 through October 3, without a year. The standing rule on the trash page is 6:00 a.m. to 8:00 p.m. This project uses 6:00 a.m. to 8:00 p.m. unless the config changes `collection_start` and `collection_end`.

### Trash and recycling overview

- URL: https://www.princegeorgescountymd.gov/departments-offices/environment/waste-recycling/trash-recycling
- Kind: HTML page
- Checked: 2026-10-06
- Points residents at an "online address tool" ("Check Your Pick-up Day" / "Lookup Your Trash Day") and at PGC311. The tool's HTML link was not extracted: automated requests to `www.princegeorgescountymd.gov`, including `/robots.txt`, returned HTTP 403 from Akamai. The machine-readable equivalent used here is the ArcGIS service below.

### PGC311

- https://www.pgc311.com/
- Phone printed by the county: 311, or (301) 883-4748 from outside the county
- A knowledge-base article at `https://pgc311.my.site.com/KnowledgePGC311/s/article/Bulky-Trash` did not return article text (the page is a client-rendered app). Bulky rules above come from the department pages, not from that article.

## ArcGIS REST

Host `gisent.princegeorgescountymd.gov` had no `robots.txt` (HTTP 404) on 2026-10-06. The query and geocode operations below returned HTTP 200 without a token.

### Trash Services map service

- Service: https://gisent.princegeorgescountymd.gov/gisonline/rest/services/DoE/Trash_Services/MapServer
- Metadata `f=json` checked 2026-10-06. Current version reported: 11.5.
- Layer 2, **Trash Services** (the layer this program queries):
  - https://gisent.princegeorgescountymd.gov/gisonline/rest/services/DoE/Trash_Services/MapServer/2
  - Query: `.../MapServer/2/query`
  - Fields: `Recycle_Day_of_Service`, `Trash_Day_of_Service1`, `Bulky_Day_of_Service`, `Yard_Day_of_Service`, `CONTRACTOR`, `Tier_Service_Area`
  - Geometry: polygons, spatial reference NAD 1983 State Plane Maryland (WKID 102685 / 2248). Queries in this program send a WGS84 point (`inSR=4326`) and `returnGeometry=false`.
  - Capabilities include Query. `maxRecordCount` 2000.
- Layer 0, `PGCOITGIS02.DBO.RRD_tiers`, also has `Trash_Day_of_Service2`. Layer 2 does not. Lookup does not call layer 0, because that would be a second map query. A household with two trash days can list both weekdays in `config.yaml`.
- Distinct values of layer 2, one statistics-style query on 2026-10-06 (`returnDistinctValues=true`, no geometry), 176 combinations:
  - Trash: Tuesday, Wednesday, Thursday, Friday, or `No Service`. No Monday value in that response.
  - Recycling: Monday, Tuesday, Wednesday, Thursday, Friday, or `No Service`. The field is a weekday, not an every-other-week flag. The default frequency is weekly. `frequency: alternate` is a manual override.
  - Yard: Monday, Tuesday (one combination), or `No Service`.
  - Bulky: Tuesday, Wednesday, Thursday, Friday, `Call 311 to Schedule`, or `No Service`.

### Address locator

- https://gisent.princegeorgescountymd.gov/gisonline/rest/services/DATA311/PGMD_Address_Locator/GeocodeServer
- Operation used: `findAddressCandidates` with `SingleLine`, `maxLocations=1`, `outSR=4326`
- Capabilities reported: Geocode, ReverseGeocode, Suggest
- One call on 2026-10-06 for the public county administration building `1301 McCormick Drive, Largo, MD 20774` returned `1301 MCCORMICK DR, UPPER MARLBORO, MD, 20774`, score 98.33, `Addr_type` PointAddress, WKID 4326.
- One spatial query of that point against layer 2 returned a single polygon: contractor `JEDA`, tier `903-1`, and `No Service` for trash, recycling, yard, and bulky. The building is inside the layer and is not on a residential route. Fixtures in `tests/fixtures/` record that public result. Tests do not call the service again.

## robots.txt and request limits

| URL | Result on 2026-10-06 |
| --- | --- |
| https://www.princegeorgescountymd.gov/robots.txt | HTTP 403, Akamai "Access Denied". No Disallow rules could be read. |
| https://gisent.princegeorgescountymd.gov/robots.txt | HTTP 404 |
| https://gis.princegeorgescountymd.gov/robots.txt | HTTP 404 |

The county HTML host was not crawled. Holiday and collection text used for the YAML and this file came from those specific public pages. The bot's lookup code never requests `www.princegeorgescountymd.gov`.

Each `pgpickup lookup` does this:

1. Return the cached JSON if it is younger than `lookup_cache_days` (default 7). Zero HTTP calls.
2. Otherwise one `findAddressCandidates` request. If there is no acceptable match, stop. No retry.
3. One layer-2 spatial query with `returnGeometry=false`. If the service sets `exceededTransferLimit`, stop instead of paging.
4. Write the cache. A third HTTP call raises an error.

Responses larger than 1 MB are rejected. The cache lives under `~/.cache/pgpickup` (or `cache_dir`) and is gitignored. It stores the address you looked up, on your machine only.

## What this project does not use

- A guessed API on the county content-management site.
- Any home address. The only street address fetched during research was the county administration building printed on the department's contact block.
- Appointment booking for appliances, tires, electronics, or scrap metal.
- Weather delays.
