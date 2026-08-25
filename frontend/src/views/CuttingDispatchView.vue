<script setup>
import { computed, reactive, ref } from 'vue'

const filters = reactive({
  project: '',
  catalog: '',
  status: 'all',
  keyword: '',
})
const dispatchForm = reactive({
  machineIp: '',
  material: '',
  thickness: '',
  count: 1,
})
const notice = ref('')
const selectedIds = ref(['preview-file-1'])

const rows = ref([
  {
    id: 'preview-file-1',
    documentNo: 'f7e50f62-d508-430e-8309-8bf309568095.GEN',
    version: '最新版本',
    pbsInfo: '--',
    operator: '--',
    operateTime: '--',
    status: '待派发',
    filePath: 'CMYZ0177\\PK-707C1\\f7e50f62-d508-430e-8309-8bf309568095.GEN',
  },
])

const selectedRows = computed(() => rows.value.filter((row) => selectedIds.value.includes(row.id)))
const activeFile = computed(() => selectedRows.value[0] || null)
const allSelected = computed(() => rows.value.length > 0 && selectedIds.value.length === rows.value.length)

function toggleAll() {
  selectedIds.value = allSelected.value ? [] : rows.value.map((row) => row.id)
}

function toggleRow(id) {
  selectedIds.value = selectedIds.value.includes(id)
    ? selectedIds.value.filter((item) => item !== id)
    : [...selectedIds.value, id]
}

function previewSearch() {
  notice.value = '当前为界面设计预览：数据库查询接口将在下一阶段接入。'
}

function previewDispatch() {
  notice.value = activeFile.value
    ? `已完成 ${activeFile.value.documentNo} 的派发参数预览；FTP 下载与魔盒推送尚未启用。`
    : '请先选择一个切割文件。'
}
</script>

<template>
  <section class="dispatch-hero">
    <div>
      <span class="eyebrow">CUTTING FILE DISPATCH</span>
      <h2>切割文件派发</h2>
      <p>从切割指令库定位最新版本文件，确认路径与目标机床后创建派发任务。</p>
    </div>
    <div class="dispatch-stage-badge">
      <span>界面原型</span>
      <strong>数据库、FTP、魔盒接口待接入</strong>
      <small>当前不会读取或推送任何生产文件</small>
    </div>
  </section>

  <div class="dispatch-flow" aria-label="派发流程">
    <div class="active"><i>01</i><span><strong>查询文件清单</strong><small>工程与目录筛选</small></span></div>
    <div><i>02</i><span><strong>定位最新文件</strong><small>解析 FTP 相对路径</small></span></div>
    <div><i>03</i><span><strong>确认派发参数</strong><small>任务与目标机床</small></span></div>
    <div><i>04</i><span><strong>推送并留痕</strong><small>魔盒任务与状态回写</small></span></div>
  </div>

  <div v-if="notice" class="dispatch-notice" role="status">
    <span>{{ notice }}</span>
    <button type="button" @click="notice = ''">关闭</button>
  </div>

  <section class="panel dispatch-query-panel">
    <div class="panel-title">
      <div><span>01</span><h2>文件清单查询</h2></div>
      <p>SQL 数据源：PDM_DesignDocumentInfo / PDM_DocumentFileInfo</p>
    </div>
    <form class="dispatch-query" @submit.prevent="previewSearch">
      <label><span>工程 / 船号</span><input v-model.trim="filters.project" placeholder="输入工程名称或船号" /></label>
      <label><span>目录层级</span><input v-model.trim="filters.catalog" placeholder="专业、分段或文件分组" /></label>
      <label><span>切割状态</span><select v-model="filters.status"><option value="all">全部状态</option><option value="pending">待派发</option><option value="sent">已派发</option><option value="failed">派发失败</option></select></label>
      <label><span>文件关键词</span><input v-model.trim="filters.keyword" placeholder="文件名 / 中间产品" /></label>
      <button class="primary" type="submit">查询文件</button>
    </form>
  </section>

  <div class="dispatch-workspace">
    <section class="panel dispatch-files-panel">
      <div class="panel-title">
        <div><span>02</span><h2>切割文件</h2></div>
        <p>结构预览 · {{ rows.length }} 条样例 · 已选 {{ selectedRows.length }} 条</p>
      </div>
      <div class="dispatch-table-wrap">
        <table class="dispatch-table">
          <thead>
            <tr><th class="check-column"><input type="checkbox" :checked="allSelected" aria-label="全选文件" @change="toggleAll" /></th><th>文件名</th><th>版本</th><th>中间产品</th><th>操作人</th><th>操作时间</th><th>切割状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id" :class="{ selected: selectedIds.includes(row.id) }" @click="toggleRow(row.id)">
              <td class="check-column"><input type="checkbox" :checked="selectedIds.includes(row.id)" :aria-label="`选择 ${row.documentNo}`" @click.stop @change="toggleRow(row.id)" /></td>
              <td class="file-name-cell"><strong>{{ row.documentNo }}</strong><small>{{ row.filePath }}</small></td>
              <td>{{ row.version }}</td><td>{{ row.pbsInfo }}</td><td>{{ row.operator }}</td><td>{{ row.operateTime }}</td><td><span class="dispatch-status">{{ row.status }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="dispatch-path-preview">
        <span>当前文件相对路径</span>
        <code>{{ activeFile?.filePath || '选择文件后显示最新版本 FilePath' }}</code>
        <small>接入 FTP 后，系统会把反斜杠转换为 FTP 路径并检查文件是否存在。</small>
      </div>
    </section>

    <aside class="panel dispatch-config-panel">
      <div class="panel-title"><div><span>03</span><h2>派发参数</h2></div></div>
      <div class="dispatch-selected-file">
        <span>当前选择</span>
        <strong>{{ activeFile?.documentNo || '尚未选择文件' }}</strong>
        <small v-if="activeFile">格式 .GEN · 待确认魔盒兼容性</small>
      </div>
      <form class="dispatch-config" @submit.prevent="previewDispatch">
        <label><span>任务名称</span><input :value="activeFile?.documentNo || ''" disabled /></label>
        <label><span>目标机床 IP</span><input v-model.trim="dispatchForm.machineIp" placeholder="例如 10.1.133.196" /></label>
        <div class="dispatch-config-row">
          <label><span>材质</span><input v-model.trim="dispatchForm.material" placeholder="可选" /></label>
          <label><span>板厚（mm）</span><input v-model="dispatchForm.thickness" type="number" min="0" step="0.01" placeholder="0" /></label>
        </div>
        <label><span>加工数量</span><input v-model="dispatchForm.count" type="number" min="1" step="1" /></label>
        <div class="dispatch-task-id"><span>任务标识</span><code>提交时由系统生成唯一 ID</code></div>
        <div class="dispatch-warning"><strong>尚未连接生产接口</strong><span>此按钮仅演示参数确认，不会下载文件或调用激光魔盒。</span></div>
        <button class="primary dispatch-submit" type="submit" :disabled="!activeFile">预览派发</button>
      </form>
    </aside>
  </div>

  <section class="panel dispatch-history-panel">
    <div class="panel-title"><div><span>04</span><h2>派发记录</h2></div><p>后续记录魔盒任务 ID、目标机床、派发人和失败原因</p></div>
    <div class="dispatch-empty-history"><i>↗</i><strong>暂无真实派发记录</strong><span>接口接入后，每次推送结果将在这里留痕并支持失败重试。</span></div>
  </section>
</template>

<style scoped>
.dispatch-hero { min-height:185px; display:flex; align-items:center; justify-content:space-between; gap:32px; }
.dispatch-hero h2 { margin:8px 0 7px; font-size:34px; }
.dispatch-hero p { margin:0; color:var(--muted); }
.dispatch-stage-badge { min-width:310px; display:flex; flex-direction:column; padding:17px 20px; border:1px solid #e2c88f; border-right:4px solid #c78a22; background:rgba(255,250,238,.9); box-shadow:0 12px 30px rgba(62,48,20,.06); }
.dispatch-stage-badge span { color:#a86b10; font-size:10px; font-weight:700; letter-spacing:2px; }
.dispatch-stage-badge strong { margin:4px 0; color:#5f4928; font-size:13px; }
.dispatch-stage-badge small { color:#89785d; }
.dispatch-flow { display:grid; grid-template-columns:repeat(4,1fr); margin-bottom:22px; border:1px solid var(--line); background:#fff; box-shadow:0 7px 22px rgba(21,54,78,.045); }
.dispatch-flow > div { position:relative; min-height:82px; display:flex; align-items:center; gap:12px; padding:17px 20px; border-right:1px solid var(--line); }
.dispatch-flow > div:last-child { border-right:0; }
.dispatch-flow > div::after { content:'›'; position:absolute; right:-7px; z-index:1; color:#b7c5cd; background:#fff; font-size:24px; }
.dispatch-flow > div:last-child::after { display:none; }
.dispatch-flow i { width:32px; height:32px; display:grid; place-items:center; flex:0 0 auto; color:#718895; border:1px solid #c6d1d8; border-radius:50%; font-style:normal; font-size:10px; }
.dispatch-flow span { display:flex; flex-direction:column; }
.dispatch-flow strong { font-size:12px; }
.dispatch-flow small { margin-top:3px; color:var(--muted); font-size:10px; }
.dispatch-flow .active { background:#f1f8fa; }
.dispatch-flow .active i { color:#fff; border-color:var(--blue); background:var(--blue); }
.dispatch-notice { display:flex; align-items:center; gap:12px; margin-bottom:22px; padding:13px 16px; color:#1f6570; border-left:4px solid var(--cyan); background:#edf8f9; font-size:12px; }
.dispatch-notice button { min-height:30px; margin-left:auto; padding:0 8px; color:var(--blue); border:0; background:transparent; }
.dispatch-query { display:grid; grid-template-columns:1.1fr 1.1fr .8fr 1fr auto; gap:14px; align-items:end; padding:24px 26px 26px; }
.dispatch-query label > span,.dispatch-config label > span { display:block; margin-bottom:7px; color:#425767; font-size:11px; font-weight:700; }
.dispatch-workspace { display:grid; grid-template-columns:minmax(0,1.8fr) minmax(300px,.72fr); gap:22px; align-items:start; }
.dispatch-files-panel,.dispatch-config-panel { min-width:0; }
.dispatch-table-wrap { overflow-x:auto; }
.dispatch-table { width:100%; min-width:830px; border-collapse:collapse; }
.dispatch-table th,.dispatch-table td { padding:13px 14px; border-bottom:1px solid #e4eaee; text-align:left; font-size:11px; }
.dispatch-table th { color:#dcecf2; background:#0b4f76; font-size:10px; letter-spacing:.4px; }
.dispatch-table tbody tr { cursor:pointer; }
.dispatch-table tbody tr:hover { background:#f5fafb; }
.dispatch-table tbody tr.selected { background:#eef8fa; box-shadow:inset 3px 0 var(--cyan); }
.dispatch-table input[type='checkbox'] { width:15px; height:15px; }
.check-column { width:42px; text-align:center!important; }
.file-name-cell { max-width:290px; }
.file-name-cell strong,.file-name-cell small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.file-name-cell strong { color:var(--navy); font-size:11px; }
.file-name-cell small { max-width:270px; margin-top:4px; color:#84939d; font:9px ui-monospace,SFMono-Regular,Menlo,monospace; }
.dispatch-status { display:inline-flex; padding:4px 8px; color:#8b5d12; border-radius:999px; background:#fff3d8; white-space:nowrap; font-size:10px; }
.dispatch-path-preview { display:flex; flex-direction:column; gap:7px; margin:18px 22px 22px; padding:15px 16px; border:1px solid #d8e3e8; background:#f7fafb; }
.dispatch-path-preview > span { color:#526a79; font-size:10px; font-weight:700; letter-spacing:1px; }
.dispatch-path-preview code { overflow-wrap:anywhere; color:#0d5d83; font:11px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace; }
.dispatch-path-preview small { color:var(--muted); font-size:10px; }
.dispatch-config-panel { position:sticky; top:18px; }
.dispatch-selected-file { display:flex; flex-direction:column; gap:4px; margin:20px 22px 0; padding:14px 15px; border-left:4px solid var(--cyan); background:#eef8fa; }
.dispatch-selected-file span { color:var(--muted); font-size:9px; letter-spacing:1px; }
.dispatch-selected-file strong { overflow:hidden; color:var(--navy); text-overflow:ellipsis; white-space:nowrap; font-size:12px; }
.dispatch-selected-file small { color:#a26917; font-size:9px; }
.dispatch-config { display:grid; gap:13px; padding:18px 22px 24px; }
.dispatch-config-row { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.dispatch-task-id { display:flex; flex-direction:column; gap:5px; padding:11px 12px; border:1px dashed #bdcbd5; background:#fafcfd; }
.dispatch-task-id span { color:#526a79; font-size:10px; font-weight:700; }
.dispatch-task-id code { color:#788a96; font-size:10px; }
.dispatch-warning { display:flex; flex-direction:column; gap:3px; padding:11px 12px; color:#7b581d; background:#fff6e4; }
.dispatch-warning strong { font-size:11px; }
.dispatch-warning span { font-size:9px; line-height:1.5; }
.dispatch-submit { width:100%; }
.dispatch-empty-history { min-height:160px; display:flex; align-items:center; justify-content:center; flex-direction:column; padding:30px; color:var(--muted); text-align:center; }
.dispatch-empty-history i { width:38px; height:38px; display:grid; place-items:center; margin-bottom:10px; color:var(--blue); border:1px solid #a9cad7; border-radius:50%; background:#f1fafb; font-style:normal; font-size:18px; }
.dispatch-empty-history strong { color:#405866; font-size:13px; }
.dispatch-empty-history span { margin-top:5px; font-size:10px; }
@media (max-width:1000px) {
  .dispatch-hero { align-items:flex-start; flex-direction:column; justify-content:center; padding:28px 0; }
  .dispatch-stage-badge { width:100%; min-width:0; }
  .dispatch-flow { grid-template-columns:1fr 1fr; }
  .dispatch-flow > div:nth-child(2) { border-right:0; }
  .dispatch-flow > div:nth-child(-n+2) { border-bottom:1px solid var(--line); }
  .dispatch-workspace { grid-template-columns:1fr; }
  .dispatch-config-panel { position:static; }
  .dispatch-query { grid-template-columns:1fr 1fr; }
}
@media (max-width:560px) {
  .dispatch-flow,.dispatch-query,.dispatch-config-row { grid-template-columns:1fr; }
  .dispatch-flow > div { border-right:0; border-bottom:1px solid var(--line); }
  .dispatch-flow > div:last-child { border-bottom:0; }
  .dispatch-flow > div::after { display:none; }
  .dispatch-query button { width:100%; }
}
</style>
