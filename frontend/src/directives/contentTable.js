// Classify values first, then derive stable column proportions from the heading
// and the currently rendered rows. This keeps one long text field from taking
// all remaining space while the table still fills its container.
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

function visualUnits(value) {
  return [...String(value || '').trim()].reduce((total, character) => (
    total + (/[^\u0000-\u00ff]/.test(character) ? 2 : character === ' ' ? 0.5 : 1)
  ), 0)
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value))
}

export function contentWidthUnits(label, values, fit = contentFit(label, values)) {
  const heading = label.replace(/\s+/g, '')
  const longest = Math.max(visualUnits(heading), ...values.map(visualUnits))
  if (/^(序号|选择)$/.test(heading)) return 6
  if (/文件名|程序号/.test(heading)) return clamp(longest + 3, 16, 28)
  if (/时间|日期|下次执行|任务开始|任务结束/.test(heading)) return clamp(longest + 3, 13, 21)
  if (/时长/.test(heading)) return clamp(longest + 3, 10, 14)
  if (/备注|错误|原因|结果|路径|收件人/.test(heading)) return clamp(longest + 3, 10, 28)
  if (/操作|状态|人员|操作人|下载人|^人$/.test(heading)) return clamp(longest + 3, 10, 18)
  if (fit === 'text') return clamp(longest + 3, 12, 26)
  return clamp(longest + 3, 8, 16)
}

function leafHeaders(table) {
  const headerRows = Array.from(table.tHead?.rows || [])
  if (!headerRows.length) return []
  return Array.from(headerRows.reduce((best, row) => (
    row.cells.length > best.cells.length ? row : best
  ), headerRows[0]).cells)
}

function applyColumnTracks(table, headers, rows) {
  if (!headers.length || table.classList.contains?.('summary-table') || !table.ownerDocument) return
  let colgroup = Array.from(table.children || []).find((child) => child.tagName === 'COLGROUP')
  if (!colgroup) {
    colgroup = table.ownerDocument.createElement('colgroup')
    table.insertBefore(colgroup, table.tHead || table.firstChild)
  }
  while (colgroup.children.length < headers.length) {
    colgroup.appendChild(table.ownerDocument.createElement('col'))
  }
  while (colgroup.children.length > headers.length) colgroup.lastElementChild.remove()

  const units = headers.map((header, index) => contentWidthUnits(
    header.textContent,
    rows.map((row) => row.cells[index].textContent),
    header.dataset.contentFit,
  ))
  const total = units.reduce((sum, value) => sum + value, 0) || 1
  Array.from(colgroup.children).forEach((column, index) => {
    column.style.setProperty('width', `${(units[index] / total * 100).toFixed(3)}%`, 'important')
  })
  table.style.setProperty('--content-table-min-width', `${Math.max(640, Math.ceil(total * 7.2))}px`)
}

function adaptContent(table) {
  table.classList.add('content-table')
  const headers = leafHeaders(table)
  // Ignore summary/empty rows with colspans when learning column contents.
  const rows = Array.from(table.tBodies).flatMap((body) => Array.from(body.rows))
    .filter((row) => row.cells.length === headers.length && Array.from(row.cells).every((cell) => cell.colSpan === 1))
  headers.forEach((header, index) => {
    const fit = contentFit(header.textContent, rows.map((row) => row.cells[index].textContent))
    header.dataset.contentFit = fit
    for (const row of rows) row.cells[index].dataset.contentFit = fit
  })
  applyColumnTracks(table, headers, rows)
}

export const contentTable = { mounted: adaptContent, updated: adaptContent }
