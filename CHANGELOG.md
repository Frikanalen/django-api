# Changelog

## [2.0.0](https://github.com/Frikanalen/django-api/compare/v1.0.0...v2.0.0) (2026-09-04)


### ⚠ BREAKING CHANGES

* **api:** Video payloads no longer include ogvUrl or largeThumbnailUrl. Read files.theora.url and files.largeThumb.url from the files map instead; both keys are absent when the file does not exist, where the old fields returned null and /static/default_large_thumbnail.png respectively.

### Features

* **api:** drop ogvUrl and largeThumbnailUrl, prune dead URL helpers ([#77](https://github.com/Frikanalen/django-api/issues/77)) ([346acc5](https://github.com/Frikanalen/django-api/commit/346acc5e6e79eef7179b279478be647794955db4))
* **video:** add dash_preview variant ([#75](https://github.com/Frikanalen/django-api/issues/75)) ([ea176e7](https://github.com/Frikanalen/django-api/commit/ea176e7da777e0ef266fc937a6a4f2ebd08058be))

## [1.0.0](https://github.com/Frikanalen/django-api/compare/v0.2.0...v1.0.0) (2026-08-27)


### ⚠ BREAKING CHANGES

* **api:** Video and schedule APIs no longer expose or accept the header field. Clients must use description instead.
* **schedule:** /api/scheduling/policy publishes each weekly slot's source under `source`, not `purpose`. The object inside is unchanged. Frikanalen/frontend#48 moves the only known client; the two land together.
* **schedule:** rename SchedulePurpose to WeeklySlotSource ([#63](https://github.com/Frikanalen/django-api/issues/63))
* **api:** /api, /api/csrf, /api/videos, /api/videofiles, /api/organization, /api/asrun, /api/categories, and /api/scheduleitems no longer redirect from their old trailing-slash form. APPEND_SLASH only adds a missing slash, it never strips one, so clients posting to the old paths will get a 404 rather than a redirect - this matters because DRF's APPEND_SLASH redirect isn't safe for non-GET requests with bodies anyway. Detail routes (e.g. /api/videos/{pk}) and non-API routes (admin, login, agenda) are unaffected.
* **api:** GET /api/jukebox_csv returns 404, and the endpoint index at GET /api/ no longer lists it.
* **api:** editor_msisdn now returns null instead of "" for an editor whose phone number is blank, and null instead of a formatted string for numbers that libphonenumber considers invalid.

### Features

* Add series support ([#60](https://github.com/Frikanalen/django-api/issues/60)) ([127a70a](https://github.com/Frikanalen/django-api/commit/127a70aeeaa5da8486c82645a36fef2ee6ea9b39))
* **api:** add weekly slot request workflows ([#74](https://github.com/Frikanalen/django-api/issues/74)) ([b993efd](https://github.com/Frikanalen/django-api/commit/b993efd6ef42e1fb2badd1c4c3ed012f2ba62551))
* **api:** enforce the schedule freeze; member picks displace fillers ([5fab7d1](https://github.com/Frikanalen/django-api/commit/5fab7d17407868b4dbd520a0b4ed516bb7910b90))
* **api:** expose the scheduling policy and displaceability to clients ([5fab7d1](https://github.com/Frikanalen/django-api/commit/5fab7d17407868b4dbd520a0b4ed516bb7910b90))
* **api:** hide organizations that have no ansvarlig redaktor ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** publish the schedule as NorDig TV-Anytime metadata ([#56](https://github.com/Frikanalen/django-api/issues/56)) ([bf3f2a0](https://github.com/Frikanalen/django-api/commit/bf3f2a0372caf93945355e37c203f2797a81c600))
* **api:** remove deprecated video header ([#72](https://github.com/Frikanalen/django-api/issues/72)) ([e08c01d](https://github.com/Frikanalen/django-api/commit/e08c01da660d41aa3c0b869b3d4f42f2806a8647))
* **api:** return MIME metadata with video files ([4a13c1f](https://github.com/Frikanalen/django-api/commit/4a13c1fb2a0094c09456778e6d1819fb9209c212))
* **api:** scope free-text video search to a named organization ([#53](https://github.com/Frikanalen/django-api/issues/53)) ([dedbea5](https://github.com/Frikanalen/django-api/commit/dedbea5e962b99aa9d9cf71f1a7c57ab48f593cb))
* **chart:** bundle the ServiceMonitor and Grafana dashboard ([8a11fa2](https://github.com/Frikanalen/django-api/commit/8a11fa26d15a74bab1d7f70cf90b30e25f2b1344))
* Config pre-commit to run ruff format and ruff check --fix ([#38](https://github.com/Frikanalen/django-api/issues/38)) ([9d9279e](https://github.com/Frikanalen/django-api/commit/9d9279eb74528b710a2fa32e446eb9249d0a1158))
* expose Prometheus metrics at /metrics ([#66](https://github.com/Frikanalen/django-api/issues/66)) ([1438c2a](https://github.com/Frikanalen/django-api/commit/1438c2a329289bd29cf4f2964df3209645a6a42d))
* **ingest:** claimable ingest jobs, and a record of what produced each file ([#69](https://github.com/Frikanalen/django-api/issues/69)) ([95542f1](https://github.com/Frikanalen/django-api/commit/95542f191d953d642a355b8ceef9547bdd89252d))
* **ingest:** report ingest state back to the uploader ([882a36f](https://github.com/Frikanalen/django-api/commit/882a36fb6e37c3cf9515406e6fb89469da2bad34))
* **jukebox:** weighted filler selection with scheduling rules ([5fab7d1](https://github.com/Frikanalen/django-api/commit/5fab7d17407868b4dbd520a0b4ed516bb7910b90))
* **models:** video and videofile timestamps are no longer nullable ([c7f94cd](https://github.com/Frikanalen/django-api/commit/c7f94cd3746f3c3d95258c076c7fdef8b5211e7f))
* **schedule:** draft slots and jukebox through the open week ([5fab7d1](https://github.com/Frikanalen/django-api/commit/5fab7d17407868b4dbd520a0b4ed516bb7910b90))
* **schedule:** expose recurring weekly slots ([#58](https://github.com/Frikanalen/django-api/issues/58)) ([6f426f5](https://github.com/Frikanalen/django-api/commit/6f426f50dc5ac70d39e8650fc50de5e2410c25de))
* **schedule:** one policy module for the broadcast-week lifecycle ([5fab7d1](https://github.com/Frikanalen/django-api/commit/5fab7d17407868b4dbd520a0b4ed516bb7910b90))
* **schedule:** publish the weekly slot's source as `source` ([#65](https://github.com/Frikanalen/django-api/issues/65)) ([c8b1482](https://github.com/Frikanalen/django-api/commit/c8b14822f1cd9f6cfc87b5cb9abcab240c6f849d))
* **schedule:** slot placements carry provenance and stay fresh ([#26](https://github.com/Frikanalen/django-api/issues/26)) ([5030d20](https://github.com/Frikanalen/django-api/commit/5030d20b652ceee0addaa74912269b33f7b92385))
* **schedule:** support frontend planning and unify drafting ([#57](https://github.com/Frikanalen/django-api/issues/57)) ([0b7f326](https://github.com/Frikanalen/django-api/commit/0b7f326d6f8a4483484760c25a27cdbc9dfbf4e1))
* **settings:** read FK_UPLOAD_URL from the environment ([893de4b](https://github.com/Frikanalen/django-api/commit/893de4bae13be3c6c0a00d0ea382cc469760bf53))
* verify video upload tokens ([8220871](https://github.com/Frikanalen/django-api/commit/822087192a019549bd31d46dbe7325028d9dce3a))
* **video:** add a dash file format ([#43](https://github.com/Frikanalen/django-api/issues/43)) ([087f34c](https://github.com/Frikanalen/django-api/commit/087f34c5f1130352e74c1e03586c8fd884dc227b))
* **video:** search video and organization names with Postgres full-text search ([#46](https://github.com/Frikanalen/django-api/issues/46)) ([d9133ab](https://github.com/Frikanalen/django-api/commit/d9133ab7fe09420836868431eb6a8221bf7e6771))


### Bug Fixes

* **admin:** name the renamed variant column in the video file inline ([#54](https://github.com/Frikanalen/django-api/issues/54)) ([858e303](https://github.com/Frikanalen/django-api/commit/858e3033a071d66e778ebdb0d11f56d10ec1c36f))
* **agenda:** repair the members' video list, which 500s on every request ([c7f94cd](https://github.com/Frikanalen/django-api/commit/c7f94cd3746f3c3d95258c076c7fdef8b5211e7f))
* **api:** anonymize accounts on delete instead of 500ing ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** consistent error shape when organization inference fails ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** hash passwords submitted to the profile endpoint ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** jukebox CSV honors proper_import like the schedule filler ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** registration no longer collects date_of_birth ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** require organization membership to create org-owned objects ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** return null for an editor with no usable phone number ([ccfcbbc](https://github.com/Frikanalen/django-api/commit/ccfcbbc9ef36b89bf9f97c5c75072e82caa858e8))
* **api:** silence OpenAPI schema warnings ([7240281](https://github.com/Frikanalen/django-api/commit/72402812b06e87742235b826a90904662bf4fda0))
* **api:** stop accepting creator attribution from the client ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **api:** type urlpatterns as patterns and resolvers ([c7f94cd](https://github.com/Frikanalen/django-api/commit/c7f94cd3746f3c3d95258c076c7fdef8b5211e7f))
* **auth:** authorize editors on organization objects ([2ce1034](https://github.com/Frikanalen/django-api/commit/2ce1034602490ed6b3a07cbe4ac2b256a3996184))
* **auth:** require approval before scheduling broadcasts ([64d18df](https://github.com/Frikanalen/django-api/commit/64d18df222657c6f7dc8296f68f45c64da8e1bed))
* bar negative durations at the database ([1a27c72](https://github.com/Frikanalen/django-api/commit/1a27c7288a10b9639402c15dd68627e379cf019f))
* **cache:** serve the page cache to the public, never to a logged-in caller ([f42c31f](https://github.com/Frikanalen/django-api/commit/f42c31f74b337f5b0a906ee884f599e6e3786477))
* **chart:** wire FK_MEDIA_URLPREFIX through to the container ([#39](https://github.com/Frikanalen/django-api/issues/39)) ([b969cc2](https://github.com/Frikanalen/django-api/commit/b969cc2c2f6fc37bdbeec8ad8d40c46b215a707a))
* **ci:** let release-please read its config so the chart version tracks releases ([784cb43](https://github.com/Frikanalen/django-api/commit/784cb43d7e7f679941d7e34ce4377ca0221da18b))
* **ci:** track uv.lock's self-version through release-please ([a47649b](https://github.com/Frikanalen/django-api/commit/a47649b70534561f2788a1813ca9260b14bc1da4))
* close the security holes and correctness bugs pinned by the test rebuild ([#23](https://github.com/Frikanalen/django-api/issues/23)) ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **config:** fix STATIC_URL after trailing slash gone ([2e1daed](https://github.com/Frikanalen/django-api/commit/2e1daed9ac882afafb4fe03a3bd555b9adc18413))
* **fk:** enforce videofile and file-format uniqueness in the schema ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **fk:** let a WeeklySlot air on its own weekday while still ahead ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **fk:** medium thumbnail lookup uses the med_thumb format name ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* grant CREATE on public schema to django db user ([ef38760](https://github.com/Frikanalen/django-api/commit/ef38760d93d0c1b37e4fd0b55602dbcaacf36e21))
* **jukebox:** re-check each placement's airtime just before saving ([5fab7d1](https://github.com/Frikanalen/django-api/commit/5fab7d17407868b4dbd520a0b4ed516bb7910b90))
* **logging:** use module loggers and lazy formatting ([ccfcbbc](https://github.com/Frikanalen/django-api/commit/ccfcbbc9ef36b89bf9f97c5c75072e82caa858e8))
* narrow raised exceptions and argument order ([ccfcbbc](https://github.com/Frikanalen/django-api/commit/ccfcbbc9ef36b89bf9f97c5c75072e82caa858e8))
* **news:** stop serving unpublished bulletins to the public ([b93a560](https://github.com/Frikanalen/django-api/commit/b93a56089e0e1e6248aeed80fe2cb109aae856eb))
* **schedule:** derive next_date from Django's timezone ([ccfcbbc](https://github.com/Frikanalen/django-api/commit/ccfcbbc9ef36b89bf9f97c5c75072e82caa858e8))
* **schedule:** one definition of occupied airtime ([1a27c72](https://github.com/Frikanalen/django-api/commit/1a27c7288a10b9639402c15dd68627e379cf019f))
* **schedule:** report an end time for zero-length items and moved ones ([#52](https://github.com/Frikanalen/django-api/issues/52)) ([9d9727f](https://github.com/Frikanalen/django-api/commit/9d9727f6812f9b98e8fb4ac36dd17878cd398a9c))
* **scheduling:** days is fractional, not whole ([c7f94cd](https://github.com/Frikanalen/django-api/commit/c7f94cd3746f3c3d95258c076c7fdef8b5211e7f))
* **scheduling:** the `latest` strategy picked by a nullable field ([bc69e7b](https://github.com/Frikanalen/django-api/commit/bc69e7bb8369395cffb142eb47989dc3dc08f8b1))
* stop the jukebox filler dying on a pool it cannot schedule from ([1a27c72](https://github.com/Frikanalen/django-api/commit/1a27c7288a10b9639402c15dd68627e379cf019f))
* **typings:** repair the environ stub and put it on mypy's path ([c7f94cd](https://github.com/Frikanalen/django-api/commit/c7f94cd3746f3c3d95258c076c7fdef8b5211e7f))
* **typings:** stub django-phonenumber-field ([c7f94cd](https://github.com/Frikanalen/django-api/commit/c7f94cd3746f3c3d95258c076c7fdef8b5211e7f))
* **video:** add webm_med to VideoFileVariant ([#45](https://github.com/Frikanalen/django-api/issues/45)) ([da06546](https://github.com/Frikanalen/django-api/commit/da06546a233eabd6341a10c96df5abe560535752))


### Performance Improvements

* **schedule:** index the schedule read paths and match airtime as a range ([#50](https://github.com/Frikanalen/django-api/issues/50)) ([6823705](https://github.com/Frikanalen/django-api/commit/68237058156bf3827242c5c41cf84149b947dba1))


### Dependencies

* update lockfile ([a616d56](https://github.com/Frikanalen/django-api/commit/a616d565f54bfa52dde4c797362e6baf4418b6f3))


### Documentation

* **api:** expose video file variants in OpenAPI ([e356f35](https://github.com/Frikanalen/django-api/commit/e356f352eaba3edc7e0ac91c17e78efbf1b9d3c8))
* **api:** list every endpoint from the API root ([#64](https://github.com/Frikanalen/django-api/issues/64)) ([7147603](https://github.com/Frikanalen/django-api/commit/71476035db1996b507051678f7cc1b77950f08f5))
* **api:** stop documenting a query parameter that does not exist ([c7f94cd](https://github.com/Frikanalen/django-api/commit/c7f94cd3746f3c3d95258c076c7fdef8b5211e7f))


### Miscellaneous Chores

* release 1.0.0 ([6fb43d3](https://github.com/Frikanalen/django-api/commit/6fb43d3c244ddee806178a213511a3908c913d2d))


### Code Refactoring

* **api:** drop trailing slashes from API routes ([#47](https://github.com/Frikanalen/django-api/issues/47)) ([cb1cd27](https://github.com/Frikanalen/django-api/commit/cb1cd2767dc4a75e9689e847a7b644f0dfddf701))
* **api:** store a video file's kind as an enum named variant ([#44](https://github.com/Frikanalen/django-api/issues/44)) ([82d85e0](https://github.com/Frikanalen/django-api/commit/82d85e052cb0071d75945d4b1d15c9121800ab35))
* **schedule:** rename SchedulePurpose to WeeklySlotSource ([#63](https://github.com/Frikanalen/django-api/issues/63)) ([4479970](https://github.com/Frikanalen/django-api/commit/4479970526999a7a2c650fb70d374ea458b9c77f))

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
