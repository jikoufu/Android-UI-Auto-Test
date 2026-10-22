import { getJSON } from "../api/client.js";
import { message, renderList } from "../components/list.js";
import { formatBytes, formatDate } from "../utils/format.js";

const byId = (id) => document.getElementById(id);

async function updateHealth() {
  const badge = byId("health");
  try {
    await getJSON("health");
    badge.textContent = "本地 API 已连接";
    badge.classList.add("ready");
  } catch {
    badge.textContent = "本地 API 未连接";
  }
}

async function updateDevices() {
  const button = byId("refresh-devices");
  button.disabled = true;
  message(byId("device-list"), "正在查询 ADB 设备…");
  try {
    const result = await getJSON("devices");
    const labels = { device: "在线", offline: "离线", unauthorized: "未授权" };
    renderList(byId("device-list"), result.devices.map((item) => ({ title: item.serial, status: labels[item.state] || item.state })), "未发现设备。请连接设备，并按需设置 ANDROID_SERIAL。");
  } catch (error) {
    message(byId("device-list"), error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function updateTasks() {
  try {
    const result = await getJSON("tasks/catalog");
    byId("scenario-count").textContent = result.scenarios.length;
    renderList(byId("task-list"), result.scenarios.map((item) => ({ title: item.title, detail: item.id, status: "命令行执行" })), "尚无测试场景。");
  } catch (error) {
    message(byId("task-list"), error.message, true);
  }
}

async function updateReports() {
  const button = byId("refresh-reports");
  button.disabled = true;
  try {
    const result = await getJSON("reports");
    byId("report-count").textContent = `${result.reports.length}${result.truncated ? "+" : ""}`;
    renderList(byId("report-list"), result.reports.map((item) => ({
      title: item.path,
      detail: `${formatBytes(item.size_bytes)} · ${formatDate(item.modified_at)}`,
      href: `/api/v1/reports/${item.path.split("/").map(encodeURIComponent).join("/")}`,
    })), "暂无报告。测试完成后，可将生成的报告保存至 runtime/reports。");
    if (result.truncated) {
      const note = document.createElement("p");
      note.className = "note";
      note.textContent = "当前显示前 100 个文件，完整目录请在本地查看。";
      byId("report-list").append(note);
    }
  } catch (error) {
    message(byId("report-list"), error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function updateCapabilities() {
  try {
    const [settings, defects] = await Promise.all([getJSON("settings"), getJSON("defects")]);
    renderList(byId("capability-list"), [
      { title: "设备配置文件", status: settings.device_config_present ? "已提供" : "待配置" },
      { title: "模型配置文件", status: settings.model_config_present ? "已提供" : "待配置" },
      { title: "AI 服务连接", status: "未验证" },
      { title: "USB IR 硬件", status: "待配置" },
      { title: "Web 任务调度", status: "待实现" },
      { title: "缺陷审核", detail: defects.detail, status: "待实现" },
    ], "暂无状态。");
  } catch (error) {
    message(byId("capability-list"), error.message, true);
  }
}

export function loadDashboard() {
  byId("refresh-devices").addEventListener("click", updateDevices);
  byId("refresh-reports").addEventListener("click", updateReports);
  // 初次加载只读取本地目录与状态，设备枚举由用户主动触发。
  return Promise.allSettled([updateHealth(), updateTasks(), updateReports(), updateCapabilities()]);
}
