import './.welding-preview-mock'
await import('./src/main')
const banner = document.createElement('div')
banner.textContent = '模拟预览 · 不连接生产库'
banner.style.cssText = 'position:fixed;right:20px;bottom:12px;z-index:9999;padding:6px 12px;border-radius:6px;background:#fff3cd;color:#815600;font-size:12px;pointer-events:none'
document.body.appendChild(banner)
