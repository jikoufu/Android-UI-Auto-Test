---
name: dev-workflow
description: "Manually invoke the project's coding workflow."
disable-model-invocation: true
argument-hint: "[start <task> <需求> | continue <id> [答复] | status/pause/resume <id> | approve <id> <sha256> --stage <阶段> --version <版本> | revise-requirements <id> <变更>]"
---

读取项目根目录的 `.agents/skills/dev-workflow/SKILL.md` 并遵循其中的说明。将以下内容作为用户参数。

$ARGUMENTS
