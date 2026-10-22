/** 通过 textContent 显示外部数据，避免将设备或报告名称解释为 HTML。 */
export function message(container, text, isError = false) {
  const paragraph = document.createElement("p");
  paragraph.className = isError ? "empty error" : "empty";
  paragraph.textContent = text;
  container.replaceChildren(paragraph);
}

export function renderList(container, rows, emptyText) {
  if (!rows.length) return message(container, emptyText);
  const list = document.createElement("ul");
  list.className = "data-list";
  for (const row of rows) {
    const item = document.createElement("li");
    item.className = "data-row";
    const title = document.createElement("span");
    title.className = "row-title";
    title.textContent = row.title;
    if (row.detail) {
      const detail = document.createElement("span");
      detail.className = "row-detail";
      detail.textContent = row.detail;
      title.append(detail);
    }
    item.append(title);
    if (row.href) {
      const link = document.createElement("a");
      link.className = "row-link";
      link.href = row.href;
      link.textContent = "下载";
      link.setAttribute("download", "");
      item.append(link);
    } else if (row.status) {
      const status = document.createElement("span");
      status.className = "status-label";
      status.textContent = row.status;
      item.append(status);
    }
    list.append(item);
  }
  container.replaceChildren(list);
}
