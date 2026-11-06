<script setup lang="ts">
import { onMounted, onActivated, onDeactivated, onBeforeUnmount } from 'vue'
import { NButton, NPagination } from 'naive-ui'
import AppIcon from '../AppIcon.vue'
import ReportPeriodPicker from '../ReportPeriodPicker.vue'
import ReportPanelState from '../ReportPanelState.vue'
import { useMyConfirmedTasks } from '../../composables/useMyConfirmedTasks'
import { formatChineseDate, formatChineseDateTime } from '../../utils/dateDisplay'

const { period, result, loading, error, load, applyPeriod, pause, activate } = useMyConfirmedTasks()
onMounted(() => load())
onActivated(activate)
onDeactivated(pause)
onBeforeUnmount(pause)
defineExpose({ refresh: () => load(undefined, { silent: true }) })
</script>

<template>
  <aside class="panel my-confirmed-tasks" aria-labelledby="my-confirmed-title" :aria-busy="loading">
    <header class="my-confirmed-tasks__heading"><h2 id="my-confirmed-title"><AppIcon name="check" size="18" />我的已确认任务</h2><NButton text :disabled="loading" @click="load()">刷新</NButton></header>
    <div class="my-confirmed-tasks__query">
      <ReportPeriodPicker :start-date="period.startDate" :end-date="period.endDate" :disabled="loading" @apply="applyPeriod" @validation-error="error = $event" />
      <p>按生产日期统计，仅显示当前设备下仍归属于我的确认任务。</p>
      <div class="my-confirmed-tasks__count"><span>已确认任务</span><strong>{{ result ? result.total : '--' }}<small>项</small></strong></div>
      <p v-if="result" class="my-confirmed-tasks__period">{{ formatChineseDate(result.start_date) }}<template v-if="result.start_date !== result.end_date"> 至 {{ formatChineseDate(result.end_date) }}</template><span v-if="loading"> · 正在更新</span></p>
    </div>
    <ReportPanelState v-if="error" type="error" title="我的确认记录读取失败" :message="error" action-label="重试" @action="load()" />
    <ReportPanelState v-else-if="loading && !result" type="loading" title="正在读取我的确认记录" />
    <ReportPanelState v-else-if="result && !result.total" type="empty" title="所选日期暂无已确认任务" message="可以选择其他生产日期查询以往记录。" />
    <ul v-if="result?.items.length" class="my-confirmed-tasks__list">
      <li v-for="item in result.items" :key="item.task_id">
        <strong>{{ item.program_no || '--' }}</strong>
        <span>{{ item.project_code?.trim() || '--' }} · {{ item.segment_name?.trim() || '--' }}</span>
        <span>{{ item.ship_no?.trim() || '--' }}</span>
        <dl><div><dt>生产日期</dt><dd>{{ formatChineseDate(item.production_date) }}</dd></div><div><dt>确认时间</dt><dd>{{ formatChineseDateTime(item.confirmed_at) }}</dd></div></dl>
      </li>
    </ul>
    <footer v-if="result && result.total > result.page_size" class="my-confirmed-tasks__pagination"><NPagination :page="result.page" :page-size="result.page_size" :item-count="result.total" :page-slot="3" :disabled="loading" @update:page="load($event)">
      <template #prev><NButton text aria-label="我的确认记录上一页" :disabled="loading || result.page <= 1">‹</NButton></template>
      <template #next><NButton text aria-label="我的确认记录下一页" :disabled="loading || result.page >= Math.ceil(result.total / result.page_size)">›</NButton></template>
      <template #label="{ type, node, active }"><NButton text :aria-label="type === 'page' ? `我的确认记录第 ${node} 页` : type === 'fast-forward' ? '我的确认记录向后翻页' : '我的确认记录向前翻页'" :aria-current="type === 'page' && active ? 'page' : undefined" :disabled="loading">{{ type === 'page' ? node : '…' }}</NButton></template>
    </NPagination></footer>
  </aside>
</template>

<style scoped>
.my-confirmed-tasks { min-width:0; margin:0; overflow:hidden; border:1px solid var(--theme-border-color); border-radius:var(--report-panel-radius); box-shadow:var(--theme-shadow); }
.my-confirmed-tasks__heading { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:16px; border-bottom:1px solid var(--theme-border-color); }
.my-confirmed-tasks__heading h2 { display:flex; align-items:center; gap:8px; margin:0; color:var(--text-color-1); font-size:var(--font-section-title); }
.my-confirmed-tasks__heading :deep(svg) { color:rgb(var(--primary-color)); }
.my-confirmed-tasks__query { padding:16px; border-bottom:1px solid var(--theme-border-color); }
.my-confirmed-tasks__query > p { margin:12px 0 0; color:var(--text-color-3); font-size:var(--font-auxiliary); line-height:1.6; }
.my-confirmed-tasks__query :deep(.report-period-picker__trigger) { white-space:normal; height:auto; min-height:40px; }
.my-confirmed-tasks__query :deep(.report-period-picker__value) { white-space:normal; overflow-wrap:anywhere; text-align:left; }
.my-confirmed-tasks__count { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-top:16px; color:var(--text-color-2); font-size:var(--font-auxiliary); }
.my-confirmed-tasks__count strong { color:rgb(var(--primary-color)); font-size:24px; font-variant-numeric:tabular-nums; }
.my-confirmed-tasks__count small { margin-left:8px; color:var(--text-color-3); font-size:var(--font-auxiliary); font-weight:400; }
.my-confirmed-tasks__list { max-height:620px; overflow:auto; list-style:none; padding:0; margin:0; }
.my-confirmed-tasks__list li { padding:16px; border-bottom:1px solid var(--theme-border-color); }
.my-confirmed-tasks__list li > strong { display:block; color:var(--text-color-1); font-size:13px; overflow-wrap:anywhere; }
.my-confirmed-tasks__list li > span { display:block; margin-top:4px; color:var(--text-color-2); font-size:var(--font-auxiliary); overflow-wrap:anywhere; }
.my-confirmed-tasks__list dl { margin:12px 0 0; color:var(--text-color-3); font-size:var(--font-auxiliary); }
.my-confirmed-tasks__list dl > div { display:flex; flex-wrap:wrap; gap:4px 12px; margin-top:4px; }
.my-confirmed-tasks__list dt { flex:0 0 auto; }.my-confirmed-tasks__list dd { margin:0; overflow-wrap:anywhere; }
.my-confirmed-tasks__pagination { padding:12px 16px; display:flex; justify-content:flex-end; }
.my-confirmed-tasks__pagination :deep(.n-button) { width:100%; height:100%; padding:0; }
.my-confirmed-tasks__pagination :deep(.n-button:focus-visible) { outline:2px solid rgb(var(--primary-color)); outline-offset:2px; }
</style>
