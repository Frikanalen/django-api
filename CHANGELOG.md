# Changelog

## [1.0.0](https://github.com/Frikanalen/django-api/compare/v0.2.0...v1.0.0) (2026-08-06)


### ⚠ BREAKING CHANGES

* **api:** editor_msisdn now returns null instead of "" for an editor whose phone number is blank, and null instead of a formatted string for numbers that libphonenumber considers invalid.

### Features

* **api:** enforce the schedule freeze; member picks displace fillers ([5fab7d1](https://github.com/Frikanalen/django-api/commit/5fab7d17407868b4dbd520a0b4ed516bb7910b90))
* **api:** expose the scheduling policy and displaceability to clients ([5fab7d1](https://github.com/Frikanalen/django-api/commit/5fab7d17407868b4dbd520a0b4ed516bb7910b90))
* **api:** hide organizations that have no ansvarlig redaktor ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **jukebox:** weighted filler selection with scheduling rules ([5fab7d1](https://github.com/Frikanalen/django-api/commit/5fab7d17407868b4dbd520a0b4ed516bb7910b90))
* **schedule:** draft slots and jukebox through the open week ([5fab7d1](https://github.com/Frikanalen/django-api/commit/5fab7d17407868b4dbd520a0b4ed516bb7910b90))
* **schedule:** one policy module for the broadcast-week lifecycle ([5fab7d1](https://github.com/Frikanalen/django-api/commit/5fab7d17407868b4dbd520a0b4ed516bb7910b90))
* **schedule:** slot placements carry provenance and stay fresh ([#26](https://github.com/Frikanalen/django-api/issues/26)) ([5030d20](https://github.com/Frikanalen/django-api/commit/5030d20b652ceee0addaa74912269b33f7b92385))


### Bug Fixes

* **api:** anonymize accounts on delete instead of 500ing ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** consistent error shape when organization inference fails ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** hash passwords submitted to the profile endpoint ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** jukebox CSV honors proper_import like the schedule filler ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** registration no longer collects date_of_birth ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** require organization membership to create org-owned objects ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** return null for an editor with no usable phone number ([ccfcbbc](https://github.com/Frikanalen/django-api/commit/ccfcbbc9ef36b89bf9f97c5c75072e82caa858e8))
* **api:** stop accepting creator attribution from the client ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **auth:** authorize editors on organization objects ([2ce1034](https://github.com/Frikanalen/django-api/commit/2ce1034602490ed6b3a07cbe4ac2b256a3996184))
* bar negative durations at the database ([1a27c72](https://github.com/Frikanalen/django-api/commit/1a27c7288a10b9639402c15dd68627e379cf019f))
* close the security holes and correctness bugs pinned by the test rebuild ([#23](https://github.com/Frikanalen/django-api/issues/23)) ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **fk:** enforce videofile and file-format uniqueness in the schema ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **fk:** let a WeeklySlot air on its own weekday while still ahead ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **fk:** medium thumbnail lookup uses the med_thumb format name ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **jukebox:** re-check each placement's airtime just before saving ([5fab7d1](https://github.com/Frikanalen/django-api/commit/5fab7d17407868b4dbd520a0b4ed516bb7910b90))
* **logging:** use module loggers and lazy formatting ([ccfcbbc](https://github.com/Frikanalen/django-api/commit/ccfcbbc9ef36b89bf9f97c5c75072e82caa858e8))
* narrow raised exceptions and argument order ([ccfcbbc](https://github.com/Frikanalen/django-api/commit/ccfcbbc9ef36b89bf9f97c5c75072e82caa858e8))
* **news:** stop serving unpublished bulletins to the public ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **schedule:** derive next_date from Django's timezone ([ccfcbbc](https://github.com/Frikanalen/django-api/commit/ccfcbbc9ef36b89bf9f97c5c75072e82caa858e8))
* **schedule:** one definition of occupied airtime ([1a27c72](https://github.com/Frikanalen/django-api/commit/1a27c7288a10b9639402c15dd68627e379cf019f))
* stop the jukebox filler dying on a pool it cannot schedule from ([1a27c72](https://github.com/Frikanalen/django-api/commit/1a27c7288a10b9639402c15dd68627e379cf019f))

## [0.2.0](https://github.com/Frikanalen/django-api/compare/v0.1.3...v0.2.0) (2026-08-04)


### Features

* **schedule:** increase page size and improve schedule handling ([f212004](https://github.com/Frikanalen/django-api/commit/f21200454a0db11f65b16f2425139c119317b1dc))

## [0.1.3](https://github.com/Frikanalen/django-api/compare/v0.1.2...v0.1.3) (2026-01-09)


### Miscellaneous Chores

* release 0.1.3 ([4eb5730](https://github.com/Frikanalen/django-api/commit/4eb5730f2eb6693793049bf4c0119bececae06e6))

## [0.1.2](https://github.com/Frikanalen/django-api/compare/v0.1.1...v0.1.2) (2025-12-18)


### Bug Fixes

* **openapi:** do not apply extra format_suffixes to drf-spectacular ([8375c78](https://github.com/Frikanalen/django-api/commit/8375c78e26a23764781763b7be2be37da4a4d58d))
* **openapi:** strategic retreat from slug to int ([4e1705c](https://github.com/Frikanalen/django-api/commit/4e1705cd469437acb94c7097bba61491fce50a13))
* use PrimaryKeyRelatedField ([283455e](https://github.com/Frikanalen/django-api/commit/283455ea75421ce312e479b876dacbd70c029c79))


### Dependencies

* add mypy as dev dep ([e1f9e80](https://github.com/Frikanalen/django-api/commit/e1f9e80d74ea2a71614135f28c381a9113f20903))

## [0.1.1](https://github.com/Frikanalen/django-api/compare/v0.1.0...v0.1.1) (2025-05-31)


### Bug Fixes

* change pagination to enable last videos view ([ac543b1](https://github.com/Frikanalen/django-api/commit/ac543b1ac97cdf72c6dfdae273dd3e0fcbd65104))

## 0.1.0 (2025-05-31)


### Bug Fixes

* change pagination to enable last videos view ([ac543b1](https://github.com/Frikanalen/django-api/commit/ac543b1ac97cdf72c6dfdae273dd3e0fcbd65104))
