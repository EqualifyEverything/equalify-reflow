# Semantic Commit

Understand the files in the `Read` section, and execute the `Run` commands then `Report` your findings.

## Read

- README.md
- .claude/commands/
- docs/infrastructure-setup.md
- project-docs/local/*
- Makefile

## Execute

- Update documentation that has become out of date due to your direct updates. 
  - Important: Do not speculatively add documentation
- Outline the work done top to bottom organized by topic of changes
- git status to see the files changed
- Map the outline of work done to the files changed and think carefully about how to best represent it as a semantic commit
- Note any AI docs (normally IN_ALL_CAPS.md and are generally status reports). We want to ignore them from final commit


## Report

Report your individual semantic commits.

For each commit, report the following:
```
X. <Commit Name>:<Commit Message>
    - <File>: <Why>
```

Do not mention exact PRD or AI documentation names in commits. We are only referencing official code and documentation.

I may provide wording/grouping feedback. If so, mirror report with changes. 

## Finally 

Once confirmed execute all the planned commits without Claude Code attribution.