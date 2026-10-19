# Swanix Autopilot

A small collection of automation scripts that run on GitHub Actions.

## What's inside

- **shield/**: scheduled audit and status page publishing
- **jobs/**: scheduled job-listing alerts
- **botbeat/**: short-form video generation
- **alpha/**: news digest bot (off by default)
- **common/**: shared helpers

## How it works

Each folder has a workflow in `.github/workflows/` that runs on a schedule or on demand. Configuration and API keys are stored as GitHub Actions secrets and are never committed to the repository.

## License

Personal project. Feel free to look around.
