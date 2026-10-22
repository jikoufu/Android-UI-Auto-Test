# 研发任务记录

`_template/` 是未执行模板，不是已完成的 ENG 任务。复制后使用团队实际分配的唯一编号；修改 `task_id` 与目录名称一致，设 `template: false`，填写标题、工作流和验收条件。

```powershell
Copy-Item -LiteralPath tasks/_template -Destination tasks/ENG-002 -Recurse
```

确认目标目录不存在再复制，避免覆盖既有任务。之后修改任务文件并运行：

```powershell
python -m agent_harness.evaluations.validate --task tasks/ENG-002
```

- `task.yaml`：范围、工作流、状态与文件位置。
- `requirement.md`：用户目标、验收条件及待确认项。
- `design.md`：涉及的模块、行为与验证方法；简单任务可说明无需单独设计。
- `verification.json`：真实检查结果，初始为 `not_run`。
- `review.md`：仅在交付或缺陷审核工作流中生成。

只提交脱敏的研发记录。运行日志、截图、UI dump 等存放到项目 `runtime/`，在记录中引用路径；清理运行产物后引用可能失效，交付时按需另行保存证据。
