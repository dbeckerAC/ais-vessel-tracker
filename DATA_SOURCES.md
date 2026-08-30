# Data sources and attribution

This repository contains application source code only. It does not include collected AIS messages, vessel history, or a database export. The MIT license applies to the application code, not to data obtained from external services.

At runtime, users may connect their own accounts or tokens for:

- [AISStream](https://aisstream.io/) — follow the provider's current API rules and terms.
- [Open Waters](https://openwaters.io/) — an aggregator that preserves the original source in each message. Follow the terms and attribution requirements shown for that source by Open Waters.

Some Open Waters feeds require specific credit, for example:

- Norwegian Coastal Administration/Kystverket: “Contains data under the Norwegian licence for Open Government data (NLOD) distributed by the Norwegian Coastal Administration.”
- Fintraffic/Digitraffic: “Source: Fintraffic / digitraffic.fi, license CC BY 4.0.”
- Volunteer receiver feeds: credit the originating station or network when one is supplied.

The application retains provider/source fields so that provenance is not discarded. Before redistributing AIS data, publishing a derived service, or using the project commercially, check the current terms for every source involved; permission to access a stream does not automatically grant unrestricted redistribution rights.

The map uses [OpenFreeMap](https://openfreemap.org/) with OpenStreetMap-derived data. MapLibre displays the style's attribution on the map.

This application is not a navigational aid. AIS coverage can be incomplete, delayed, duplicated, or incorrect.
