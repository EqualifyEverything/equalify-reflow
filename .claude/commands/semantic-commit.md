# Semantic Commit

Understand the files in the `Read` section, and execute the `Run` commands then `Report` your findings.

## Read

- README.md
- Makefile

## Execute

- Run `uv run ruff check` and `uv run mypy` on Python files being committed
  - Fix any lint issues before proceeding with the commit
- Update documentation that has become out of date due to your direct updates.
  - Important: Do not speculatively add documentation
- Outline the work done top to bottom organized by topic of changes
- git status to see the files changed
- Map the outline of work done to the files changed and think carefully about how to best represent it as a semantic commit
- Note any AI docs (normally IN_ALL_CAPS.md and are generally status reports). We want to ignore them from final commit

## Report

Report your individual semantic commits.

For each commit, report the following:

```markdown
X. <Commit Name>:<Commit Message>
    - <File>: <Why>
```

Do not mention exact PRD or AI documentation names in commits. We are only referencing official code and documentation.

I may provide wording/grouping feedback. If so, mirror report with changes.

## Finally

Once confirmed execute all the planned commits without Claude Code attribution.
First stage the changed files and then in a separate command, commit them.
This order is important because staging doesn't require permission, but commits do
and I want to review the work before final commit.
