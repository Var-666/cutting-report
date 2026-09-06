// CSS does the actual sizing. These hints keep short fields compact without
// measuring pixels, fixing column proportions, or resizing on every scroll.
export function contentFit(label, values) {
  const heading = label.replace(/\s+/g, '')
  const populated = values.map((value) => value.trim()).filter((value) => value && !/^[-—–]+$/.test(value))
  if (/^(序号|选择|人|操作|任务数|数量|大小|尝试)$/.test(heading)) return 'compact'
  if (/时间|日期|时长|下次执行|任务开始|任务结束/.test(heading)) return 'compact'
  if (!populated.length) return 'compact'
  if (populated.every((value) => /^[+-]?[\d,]+(?:\.\d+)?(?:%|\s*(?:KB|MB|GB|B|kWh|Nm³))?$/.test(value))) return 'compact'
  if (populated.every((value) => [...value].length <= 8)) return 'compact'
  return 'text'
}

function adaptContent(table) {
  table.classList.add('content-table')
  const headers = Array.from(table.tHead?.rows[0]?.cells || [])
  // Ignore summary/empty rows with colspans when learning column contents.
  const rows = Array.from(table.tBodies).flatMap((body) => Array.from(body.rows))
    .filter((row) => row.cells.length === headers.length && Array.from(row.cells).every((cell) => cell.colSpan === 1))
  headers.forEach((header, index) => {
    const fit = contentFit(header.textContent, rows.map((row) => row.cells[index].textContent))
    header.dataset.contentFit = fit
    for (const row of rows) row.cells[index].dataset.contentFit = fit
  })
}

export const contentTable = { mounted: adaptContent, updated: adaptContent }
