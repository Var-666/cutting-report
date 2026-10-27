import type { ObjectDirective } from 'vue'
// Derive an intrinsic width for every column, then add an equal share of the
// remaining container width to every track. A real table plus a generated
// colgroup keeps headers, body cells and summary rows on exactly the same grid.
const MIN_TABLE_WIDTH = 560
const PIXELS_PER_UNIT = 7.8
const tableObservers = new WeakMap<HTMLTableElement, ResizeObserver>()

export function contentFit(label: string, values: string[]) {
  const heading = label.replace(/\s+/g, '')
  const populated = values.map((value) => value.trim()).filter((value) => value && !/^[-—–]+$/.test(value))
  if (/^(序号|选择|人|待确认|操作|任务数|数量|大小|尝试)$/.test(heading)) return 'compact'
  if (/时间|日期|时长|下次执行|任务开始|任务结束/.test(heading)) return 'compact'
  if (!populated.length) return 'compact'
  if (populated.every((value) => /^[+-]?[\d,]+(?:\.\d+)?(?:%|\s*(?:KB|MB|GB|B|kWh|Nm³))?$/.test(value))) return 'compact'
  if (populated.every((value) => [...value].length <= 8)) return 'compact'
  return 'text'
}

function visualUnits(value: string) {
  return [...String(value || '').trim()].reduce((total, character) => (
    total + (/[^\u0000-\u00ff]/.test(character) ? 2 : character === ' ' ? 0.5 : 1)
  ), 0)
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value))
}

export function contentWidthUnits(label: string, values: string[], fit = contentFit(label, values)) {
  const heading = label.replace(/\s+/g, '')
  const longest = Math.max(visualUnits(heading), ...values.map(visualUnits))
  if (/^(序号|选择)$/.test(heading)) return 6
  if (/文件名|程序号/.test(heading)) return clamp(longest + 4, 18, 42)
  if (/时间|日期|下次执行|任务开始|任务结束/.test(heading)) return clamp(longest + 4, 16, 30)
  if (/时长/.test(heading)) return clamp(longest + 4, 10, 16)
  if (/备注|错误|原因|结果|路径|收件人/.test(heading)) return clamp(longest + 4, 12, 42)
  if (/操作|状态|人员|操作人|下载人|待确认|^人$/.test(heading)) return clamp(longest + 4, 11, 28)
  if (fit === 'text') return clamp(longest + 4, 12, 36)
  return clamp(longest + 4, 8, 22)
}

export function distributeColumnWidths(units: number[], availableWidth = 0) {
  if (!units.length) return { intrinsicWidths: [], widths: [], tableWidth: 0 }
  const intrinsicWidths = units.map((unit) => Math.ceil(unit * PIXELS_PER_UNIT))
  const intrinsicTotal = intrinsicWidths.reduce((sum, width) => sum + width, 0)
  const tableWidth = Math.max(MIN_TABLE_WIDTH, Math.floor(availableWidth || 0), intrinsicTotal)
  const sharedRemainder = Math.max(0, tableWidth - intrinsicTotal) / units.length
  const widths = intrinsicWidths.map((width) => width + sharedRemainder)
  return { intrinsicWidths, widths, tableWidth }
}

function leafHeaders(table: HTMLTableElement) {
  const headerRows = Array.from(table.tHead?.rows || [])
  if (!headerRows.length) return []
  return Array.from(headerRows.reduce((best, row) => (
    row.cells.length > best.cells.length ? row : best
  ), headerRows[0]).cells)
}

function applyColumnTracks(table: HTMLTableElement, headers: HTMLTableCellElement[], rows: HTMLTableRowElement[]) {
  if (!headers.length) return
  const units = headers.map((header, index) => {
    const contentUnits = contentWidthUnits((header.textContent || ''), rows.map((row) => (row.cells[index].textContent || '')), header.dataset.contentFit)
    const view = table.ownerDocument?.defaultView
    if (header.dataset.contentAction !== 'true' || !view) return contentUnits
    // Button padding and gaps are wider than their labels; reserve their actual width.
    const actionWidths = rows.map((row) => {
      const cell = row.cells[index]
      const style = view.getComputedStyle(cell)
      const width = Array.from(cell.children).reduce((total, child) => {
        const childStyle = view.getComputedStyle(child)
        return total + child.getBoundingClientRect().width + (parseFloat(childStyle.marginLeft) || 0) + (parseFloat(childStyle.marginRight) || 0)
      }, 0)
      return width + parseFloat(style.paddingLeft) + parseFloat(style.paddingRight)
    })
    return Math.max(contentUnits, ...actionWidths.map((width) => width / PIXELS_PER_UNIT))
  })
  const availableWidth = table.parentElement?.clientWidth || table.clientWidth || 0
  const tracks = distributeColumnWidths(units, availableWidth)
  table.style?.setProperty('--content-table-min-width', `${tracks.tableWidth}px`)
  if (!table.ownerDocument) return

  let colgroup = Array.from(table.children || []).find((child) => child.tagName === 'COLGROUP') as HTMLTableColElement | undefined
  if (!colgroup) {
    colgroup = table.ownerDocument.createElement('colgroup')
    colgroup.dataset.contentTableGenerated = 'true'
    table.insertBefore(colgroup, table.tHead || table.firstChild)
  }
  while (colgroup.children.length < headers.length) {
    colgroup.appendChild(table.ownerDocument.createElement('col'))
  }
  while (colgroup.children.length > headers.length) colgroup.lastElementChild?.remove()
  Array.from(colgroup.children).forEach((element, index) => {
    const column = element as HTMLTableColElement
    column.style.setProperty('width', `${tracks.widths[index].toFixed(2)}px`, 'important')
  })
}

function adaptContent(table: HTMLTableElement) {
  table.classList.add('content-table')
  const headers = leafHeaders(table)
  // Include regular footer totals, but ignore empty/summary rows that merge
  // columns because those values do not describe one individual track.
  const sections = [...Array.from(table.tBodies), table.tFoot].filter((section): section is HTMLTableSectionElement => Boolean(section))
  const rows = sections.flatMap((section) => Array.from(section.rows))
    .filter((row) => row.cells.length === headers.length && Array.from(row.cells).every((cell) => cell.colSpan === 1))
  const columns = table.dataset?.contentColumns?.split(' ') || []
  headers.forEach((header, index) => {
    const fit = contentFit((header.textContent || ''), rows.map((row) => (row.cells[index].textContent || '')))
    const column = columns[index] || 'left'
    const align = column === 'actions' ? 'center' : column
    header.dataset.contentAlign = align
    header.dataset.contentAction = column === 'actions' ? 'true' : ''
    header.dataset.contentFit = fit
    for (const row of rows) {
      const cell = row.cells[index]
      cell.dataset.contentFit = fit
      cell.dataset.contentAlign = align
      cell.dataset.contentAction = header.dataset.contentAction || ''
      if (!cell.title || cell.title === cell.dataset.contentTitle) {
        cell.title = (cell.textContent || '').trim()
        cell.dataset.contentTitle = cell.title
      }
    }
  })
  applyColumnTracks(table, headers, rows)
}

function observeContainer(table: HTMLTableElement) {
  if (typeof ResizeObserver === 'undefined' || !table.parentElement || tableObservers.has(table)) return
  const observer = new ResizeObserver(() => {
    if (table.isConnected === false) {
      stopObserving(table)
      return
    }
    adaptContent(table)
  })
  observer.observe(table.parentElement)
  tableObservers.set(table, observer)
}

function stopObserving(table: HTMLTableElement) {
  tableObservers.get(table)?.disconnect()
  tableObservers.delete(table)
}

export const contentTable: ObjectDirective<HTMLTableElement, boolean | undefined> = {
  mounted(table, binding) {
    if (binding?.value === false) return
    adaptContent(table)
    observeContainer(table)
  },
  updated(table, binding) {
    if (binding?.value === false) {
      stopObserving(table)
      return
    }
    adaptContent(table)
    observeContainer(table)
  },
  beforeUnmount: stopObserving,
  unmounted: stopObserving,
}
