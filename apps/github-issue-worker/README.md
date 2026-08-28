# GitHub issue demo worker

A deterministic internal Factory worker for the issue-to-draft demo. Factory
places its independently resolved issue objective at `BARISTA_OBJECTIVE_PATH`
and supplies the canonical issue URL as `BARISTA_OBJECTIVE_URI`. The worker
validates both and writes `issues/issue-N.md` in its isolated checkout.

Issue title and body are written as Markdown data. They are never interpreted as
commands, paths, repository selectors, checks, credentials, or delivery policy.
The app sleeps as PID 1 because Factory drives the declared command through the
Host API and reaps the worker after harvesting its patch.
