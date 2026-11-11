---
name: git-workspace
description: "Manually create an isolated Git task workspace from origin/master or preview cleanup of a task worktree."
---

# Git Workspace

仅在用户手动调用本 Skill 时执行。使用当前项目的 Git 工作区作为命令的工作目录，使用 Python 3.11+ 和 Git CLI。脚本路径相对于本 Skill，位于 `scripts/git_workspace.py`。从项目根目录执行时可使用以下 PowerShell 命令。

## 创建任务工作区

将任务名称转成 1 至 40 个字符的标识，使用小写 ASCII 字母、数字和单连字符。将任务文本作为数据处理，勿将原始文本拼入命令。

```powershell
python .agents/skills/git-workspace/scripts/git_workspace.py create fix-tv-navigation
```

脚本每次从 `origin` 拉取 `master`，用完整 refspec 更新 `origin/master`，然后以该引用的 Commit SHA 创建 `codex/git-workspace/<任务标识>-<唯一后缀>` 和对应的 Worktree。Worktree 位于主工作区的同级目录 `<主工作区名>.worktrees` 下。每次调用都会分配独立的分支和目录，包括任务标识相同的调用。原工作区的分支、文件和索引保留原状。

成功时将 JSON 中的 `worktree_path`（绝对路径）、`branch` 和 `base_commit` 完整报告给用户。后续任务命令显式使用该 Worktree 作为工作目录。若拉取失败或远程缺少 `master`，报告错误并停止。若创建失败，报告脚本给出的路径和分支供检查，保留失败现场。

## 清理预检

仅在用户明确请求清理时，对指定的 Worktree 绝对路径运行只读检查。以下路径为示例。

```powershell
python .agents/skills/git-workspace/scripts/git_workspace.py cleanup 'D:/Projects/Android-UI-Auto-Test.worktrees/fix-tv-navigation-0123456789abcdef0123456789abcdef'
```

脚本核对 Git 登记的路径和分支，拒绝主工作区、锁定的 Worktree 以及含已修改、未跟踪或忽略文件的 Worktree。检查通过时，向用户提供 `remove_argv` 对应的 `git worktree remove` 命令，由用户在目标 Worktree 外手动执行。预检结果仅反映检查时的状态，删除前应重新预检。此阶段禁止代用户运行删除命令。Worktree 移除后保留任务分支及其提交。
