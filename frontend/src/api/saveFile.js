export function canChooseSaveLocation(env = globalThis) {
  return env.isSecureContext === true && typeof env.showSaveFilePicker === 'function'
}

// The picker must open in the click handler, before fetching the file. Awaiting
// a network request first can lose the browser's transient user activation.
export async function saveFileFromUrl(url, filename, { browserDownload = false } = {}, env = globalThis) {
  let handle
  if (!browserDownload) {
    if (!canChooseSaveLocation(env)) throw new Error('当前浏览器不支持网页选择路径，请使用浏览器下载方式。')
    try {
      handle = await env.showSaveFilePicker({ suggestedName: filename, id: 'cutting-files' })
    } catch (error) {
      if (error.name === 'AbortError') return { mode: 'cancelled' }
      throw new Error('无法打开保存窗口，请检查浏览器权限后重试。')
    }
  }

  const response = await env.fetch(url, { credentials: 'same-origin' })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new Error(typeof payload?.detail === 'string' ? payload.detail : '本地文件读取失败，请重新下载')
  }
  // Read successfully before opening the writable file, so HTTP/read failures
  // cannot replace an existing destination with an empty or partial download.
  const blob = await response.blob()
  if (handle) {
    let writable
    try {
      writable = await handle.createWritable()
      await writable.write(blob)
      await writable.close()
    } catch (error) {
      if (writable) await writable.abort().catch(() => {})
      throw new Error('文件未保存成功，请检查目标文件夹权限和磁盘空间后重试。')
    }
    return { mode: 'picker', filename: handle.name || filename }
  }

  const objectUrl = env.URL.createObjectURL(blob)
  const anchor = env.document.createElement('a')
  try {
    anchor.href = objectUrl
    anchor.download = filename
    env.document.body.appendChild(anchor)
    anchor.click()
  } finally {
    anchor.remove()
    env.setTimeout(() => env.URL.revokeObjectURL(objectUrl), 60000)
  }
  return { mode: 'browser', filename }
}
