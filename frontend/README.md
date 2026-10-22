# 本地控制台

纯 HTML、CSS 与 JavaScript ES Modules，按推荐结构划分 `js/api`、`js/components`、`js/pages`、`js/utils`。由 FastAPI 在同源 `/` 提供，无需 Node.js 构建。

在项目根目录运行 `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`，浏览器访问 `http://127.0.0.1:8000`。不要直接打开 HTML 文件，因为页面通过同源 API 查询数据。

已提供场景目录、报告附件下载、能力状态，以及手动触发的 ADB 设备查询。Web 任务创建和调度、缺陷管理、配置编辑、权限与远程部署尚未实现。页面不会启动测试或 Agent；设备在线状态和配置文件存在不代表测试或服务连接已经验证。
