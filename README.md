# fkweb

Backend API for the Norwegian public access TV channel [Frikanalen](https://frikanalen.tv/).

## Configuration

### Environment variables

If the server is run with `uv run manage.py runserver`, .env and .env.local are read, with .env.local taking precedence. If it is run in wsgi, only .env is used.

- ALLOWED_HOSTS - comma-separated list of permitted domains
- DATABASE_URL - database URL
- CACHE_URL - cache URL
- SMTP_SERVER - smtp server for outgoing email

## Installation

### Docker

To build a local copy:

```sh
docker build -t frikanalen/django-api .
```

Then you can run it thus:

```sh
docker run -p 8080:8080 frikanalen/django-api
```

### Local development

#### Use in Docker

If you set the environment variables `$DJANGO_SUPERUSER_EMAIL` and `$DJANGO_SUPERUSER_PASSWORD`, the user will be created if it does not exist.

#### Initializing dev environment

Install the [uv package manager](https://docs.astral.sh/uv/getting-started/installation/).

```sh
# Create the virtual environment
uv venv
# Activate it
source .venv/bin/activate
# Install the packages in the lockfile
uv sync
```

##### Git hooks (pre-commit)

This repository includes a `.pre-commit-config.yaml` that runs `ruff format` and `ruff check --fix` on staged Python files.

Enable hooks using the project's uv workflow (recommended):

```sh
# Install pre-commit via uv's tool manager and enable the pre-commit uv plugin
uv tool install pre-commit --with pre-commit-uv
# Install the git hooks
pre-commit install
# Optionally run the hooks across the entire repository
uv run pre-commit run --all-files
```

Notes:
- `uv tool install ... --with pre-commit-uv` installs the pre-commit tool and the `pre-commit-uv` helper so pre-commit hooks can use tools managed by `uv`.
- `pre-commit install` registers the hooks with Git for this repository.
- `uv run pre-commit run --all-files` runs the configured hooks across the repository using uv-managed tools.

pre-commit normally creates isolated venvs for each hook; installing the `pre-commit-uv` helper lets hooks use tools installed and managed by `uv` instead, if configured that way.

If you'd like the hooks to always run Ruff via `uv run ruff` rather than the hook venvs, the configuration can be adjusted — tell me and I'll update the `.pre-commit-config.yaml` accordingly.

#### Initializing database

```sh
# Spin up PostgreSQL in Docker; web interface now at localhost:8082
docker-compose up -d
# Initialize the database
./manage.py migrate
# Load necessary fixtures (eg. content categories) into the database:
./manage.py loaddata frikanalen
```

**EITHER** Load the testing users and organizations:

```sh
./manage.py loaddata test-users
```

**OR** Create a new admin user:

```sh
./manage.py createsuperuser
```

Start the webserver:

```shell
./manage.py runserver
```

Point your browser to http://127.0.0.1:8000/admin and log in.

#### Running tests

The test suite uses pytest with pytest-django. Existing Django `TestCase` and DRF
`APITestCase` tests are collected alongside pytest-style tests.

```sh
uv run pytest
```

To run a specific test module or directory, pass its path to pytest:

```sh
uv run pytest api/schedule
```

To run the full suite with branch coverage and missing-line reporting:

```sh
uv run pytest --cov
```

## Schedule feeds

The broadcast schedule is published in two machine-readable formats.

- **NorDig TV-Anytime** at `/api/tvanytime/upcoming` (and
  `/api/tvanytime/YYYY/MM/DD`), indexed at `/api/tvanytime`,
  which is what Nordic distributors pull an EPG in. It describes each
  programme once, says separately when it airs and whether it is available
  on demand, and carries genre, subtitles, credits and actual broadcast
  times. See [docs/tvanytime.md](docs/tvanytime.md), and
  [docs/tvanytime-model-proposals.md](docs/tvanytime-model-proposals.md)
  for the model changes that would enrich it further.
- **XMLTV** at `/xmltv/upcoming/` (and `/xmltv/YYYY/MM/DD`), a flat list of
  programme slots, kept for the consumers already using it.

## Scheduling: airtime grants

Organizations no longer edit the broadcast schedule. Staff grant them
windows of airtime (`scheduling.AirtimeGrant`), each organization fills in
the list of what plays in its window, and a compiler turns locked lists into
`Scheduleitem` rows. Unprogrammed airtime is left to playout's graphics. Only
the compiler and staff write `Scheduleitem`. See `scheduling/services.py`,
which owns every state change, and `scheduling/broadcast_time.py`, which owns
every wall-clock (Europe/Oslo) conversion.

Grants come from recurring slots (daily, weekly, fortnightly, monthly...),
from staff directly, or later from approved requests. A grant locks the
Monday before its week airs; staff can exempt it from locking. A grant with a
fill rule fills itself (pool: an organization's uploads, a playlist or the
filler pool; ordering: newest, least aired, shuffled or weighted; mode: one
video or fill the window). Filling is deterministic, so the nightly run only
rewrites a list when something real changed.

The nightly CronJob (00:05 `Europe/Oslo`, overlapping runs forbidden) runs
the pipeline: issue grants for every slot occurrence through the end of the
week after next, refill automatic grants, lock grants past their deadline,
and compile locked grants.

```sh
./manage.py draft_broadcast_schedule
```

### Cut-over

The first deploy of grants converts every weekly slot into a weekly
recurring slot (with its source as a fill rule) but leaves the existing
schedule alone. The cut-over starts the new schedule: it deletes every
pre-cut-over schedule item (any row with a `schedulereason`) starting at or
after the given instant, then runs the pipeline as of then, so this and next
week's slot grants are filled and compiled at once. Rows written under the new
rules are kept, so it is safe to re-run.

```sh
./manage.py cutover_schedule --at 2026-11-20T09:00+01:00 --yes
```

Grants of unowned slots go to the station organization,
`STATION_ORGANIZATION_ID` (default 3).

## Test data

As a convenience a test data file has been supplied, eg. for integration testing.

It contains the following organizations:

- dev-org1
- dev-org2

Additionally, the following users:

- dev-admin@frikanalen.tv _site administrator_
- dev-org1-admin@frikanalen.tv _administrator for org1_
- dev-org1-member@frikanalen.tv _member of org1_
- dev-org2-admin@frikanalen.tv _administrator for org2_

For more advanced things you'd want to check [our infrastructure Ansible setup](../../infra/README.md).
