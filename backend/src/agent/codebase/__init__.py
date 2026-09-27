"""Codebase analysis now uses Semgrep.

The custom scanner this package used to hold (GitHub API client, framework
detector, regex pattern matcher and ORM-smell orchestrator) has been removed.
Detection is driven by the Semgrep rule set in ``rules/slowtrace-rules.yml``,
which the Semgrep wrapper runs over a local clone of the target repository.
"""
