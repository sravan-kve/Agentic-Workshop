- source_spec: `_bmad-output/specs/spec-epic-1/stories/1-triage-decision-schema.md`
  summary: Keep IDE and OS files (`.idea/`, `.DS_Store`) out of the repo.
  evidence: `.idea/` files are staged and `.DS_Store` is untracked in the working tree; they predate this story and are outside its diff. Ignore them locally or in `.gitignore` in a separate change.
