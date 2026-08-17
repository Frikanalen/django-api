# fkweb

Backend API for the Norwegian public access TV channel [Frikanalen](https://frikanalen.no/).

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

## Management commands

In addition to the HTTP API, the following commands are executed periodically as Kubernetes cron jobs in our cluster. Together they implement the broadcast-week lifecycle defined in `agenda/scheduling/policy.py`: every week is drafted two Mondays before it airs, stays open for member organizations to replace jukebox fillers with picks of their own for one week, and is frozen from the Monday before airing.

```sh
./manage.py fill_next_weeks_agenda
```

This job places videos as defined by the WeeklySlot model, for every slot occurrence up to the end of the open broadcast week. This will generally be entries like "Fill Mondays 12-13 with the latest videos from NUUG".

```sh
./manage.py fill_agenda_with_jukebox
```

This job fills the remaining unpopulated airtime up to the end of the open broadcast week, drawing from the videos marked with is_filler=True by a weighted-random draw that prefers fresh uploads and organizations with little airtime that week (see `agenda/scheduling/selection.py`).

## Test data

As a convenience a test data file has been supplied, eg. for integration testing.

It contains the following organizations:

- dev-org1
- dev-org2

Additionally, the following users:

- dev-admin@frikanalen.no _site administrator_
- dev-org1-admin@frikanalen.no _administrator for org1_
- dev-org1-member@frikanalen.no _member of org1_
- dev-org2-admin@frikanalen.no _administrator for org2_

For more advanced things you'd want to check [our infrastructure Ansible setup](../../infra/README.md).
