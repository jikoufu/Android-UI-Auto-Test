/** 同源、只读 API 客户端；请求都有明确超时。 */
export async function getJSON(path) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(`/api/v1/${path}`, { signal: controller.signal, headers: { Accept: "application/json" } });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : `查询失败（HTTP ${response.status}）`);
    }
    return await response.json();
  } catch (error) {
    if (error.name === "AbortError") throw new Error("查询超时，请检查本地后端后重试。");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
