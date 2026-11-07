<script setup lang="ts">
import { computed } from 'vue'
import { NProgress } from 'naive-ui'
import AppReportTable from '../AppReportTable.vue'
import type { CuttingMachine } from '../../types/cutting'
import type { PushBatchItem } from '../../utils/cuttingDispatchBatch'
import type { PushBatchState } from '../../composables/useCuttingDispatchBatch'
import { batchItemError, hasNameMismatch, batchStatusLabel, isStaleFileError } from '../../utils/cuttingDispatchBatch'

const props = defineProps<{
  pushBatch: PushBatchState
  machines: CuttingMachine[]
  machineName: (ip: string) => string
  canPush: boolean
}>()
const machineIp = defineModel<string>('machineIp', { required: true })
const bulkQuantity = defineModel<number>('bulkQuantity', { required: true })
const emit = defineEmits<{
  close: []; submit: []; applyQuantity: []; refreshCatalog: []
  parametersChanged: [item: PushBatchItem]; remove: [item: PushBatchItem]
  retry: [item: PushBatchItem]; check: [item: PushBatchItem]
}>()
const invalidBatchCount = computed(() => props.pushBatch.items.filter(batchItemError).length)
const nonStandardBatchCount = computed(() => props.pushBatch.items.filter(item => !item.parsed).length)
const overrideBatchCount = computed(() => props.pushBatch.items.filter(hasNameMismatch).length)
const batchStatusCounts = computed(() => {
  const counts: Record<string, number> = { sent: 0, duplicate: 0, failed: 0, pending: 0 }
  props.pushBatch.items.forEach(item => { counts[Object.hasOwn(counts, item.status) ? item.status : 'pending'] += 1 })
  return counts
})

const batchTableColumns = [
  { key: 'c0', title: "文件名", minWidth: 220, align: 'left', fixed: 'left' },
  { key: 'c8', title: "命名检查", width: 104, align: 'center' },
  { key: 'c9', title: "识别材质", width: 96, align: 'left' },
  { key: 'c10', title: "识别板厚（mm）", width: 128, align: 'right' },
  { key: 'c1', title: "分段名", width: 140, align: 'left', ellipsis: false },
  { key: 'c2', title: "材质", width: 120, align: 'left', ellipsis: false },
  { key: 'c3', title: "板厚（mm）", width: 128, align: 'right', ellipsis: false },
  { key: 'c4', title: "数量", width: 112, align: 'right', ellipsis: false },
  { key: 'c11', title: "参数核对", minWidth: 260, align: 'left' },
  { key: 'c5', title: "派发状态", width: 160, align: 'center' },
  { key: 'c12', title: "任务编号", width: 240, align: 'left' },
  { key: 'c13', title: "处理说明", minWidth: 260, align: 'left' },
  { key: 'c6', title: "操作", width: 300, align: 'center', fixed: 'right', ellipsis: false }
] as const
</script>

<template>
  <NModal :show="pushBatch.visible" :mask-closable="!pushBatch.saving" :close-on-esc="!pushBatch.saving" @update:show="visible => { if (!visible) emit('close') }">
      <div class="push-batch-dialog" role="dialog" aria-modal="true" aria-labelledby="push-batch-title" tabindex="-1">
        <header class="push-batch-header">
          <h2 id="push-batch-title">{{ pushBatch.items.length === 1 ? '文件派发确认' : '批量派发确认' }}</h2>
          <NButton attr-type="button" :disabled="pushBatch.saving" aria-label="关闭批量推送窗口" @click="emit('close')">×</NButton>
        </header>

        <div class="push-batch-content">
          <div class="push-batch-common">
            <label><span>目标机床</span><AppSelect v-model="machineIp" :disabled="pushBatch.saving || pushBatch.resultsReady"><option value="" disabled>请选择目标机床</option><option v-for="machine in machines" :key="machine.machine_ip" :value="machine.machine_ip">{{ machineName(machine.machine_ip) }} · {{ machine.machine_ip }}（{{ machine.online ? '在线' : '未从列表确认在线' }}）</option></AppSelect></label>
            <label><span>统一加工数量</span><div><NInputNumber v-model:value="bulkQuantity" name="bulk_quantity" :min="1" :max="10000" :step="1" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" :input-props="{ 'name': 'bulk_quantity', 'autocomplete': 'off' }" :precision="0" /><NButton attr-type="button" :disabled="pushBatch.saving || pushBatch.resultsReady" @click="emit('applyQuantity')">应用到全部</NButton></div></label>
            <div class="push-batch-summary"><strong>{{ pushBatch.items.length }}</strong><span>个文件</span><small v-if="!pushBatch.resultsReady">{{ invalidBatchCount ? `${invalidBatchCount} 项待补参数` : nonStandardBatchCount || overrideBatchCount ? `${nonStandardBatchCount} 项非标准名、${overrideBatchCount} 项参数不符待确认` : '全部为标准名' }}</small><small v-else>已推送 {{ batchStatusCounts.sent }} · 失败 {{ batchStatusCounts.failed }} · 重复 {{ batchStatusCounts.duplicate }}</small></div>
          </div>

          <div v-if="pushBatch.error" class="push-batch-error" role="alert">{{ pushBatch.error }}</div>
          <div v-if="pushBatch.saving" class="push-batch-progress" role="status"><div><strong>{{ pushBatch.currentFilename }}</strong><span>{{ pushBatch.completed }} / {{ pushBatch.total }}</span></div><NProgress type="line" :percentage="pushBatch.total ? Math.round(pushBatch.completed / pushBatch.total * 100) : 0" :show-indicator="false" /><small>正在按顺序处理，请不要关闭页面。</small></div>

          <div class="push-batch-table-wrap">
            <AppReportTable class="push-batch-table" :columns="batchTableColumns" :items="pushBatch.items" :row-key="item => item.referenceKey" :paginate="false" :row-class-name="item => (!pushBatch.resultsReady && (batchItemError(item) || !item.parsed || hasNameMismatch(item))) ? 'invalid' : ''" :loading="pushBatch.saving">
        <template #c0="{ row: item }"><strong class="push-batch-file">{{ item.filename }}</strong></template>
        <template #c8="{ row: item }"><span class="filename-standard" :class="{ warning: !item.parsed }">{{ item.parsed ? '标准名' : '非标准名' }}</span></template>
        <template #c9="{ row: item }">{{ item.parsed?.material || '--' }}</template>
        <template #c10="{ row: item }">{{ item.parsed ? item.parsed.thickness.toFixed(1) : '--' }}</template>
        <template #c11="{ row: item }"><span class="push-batch-review" :class="{ manual: batchItemError(item) || !item.parsed || hasNameMismatch(item) }">{{ batchItemError(item) || (hasNameMismatch(item) ? '加工参数与标准名不一致，请人工复核' : item.parsed ? '与标准名一致' : '非标准名，请人工核对参数') }}</span></template>
        <template #c12="{ row: item }">{{ item.dispatchId || '--' }}</template>
        <template #c13="{ row: item }">{{ item.error || '--' }}</template>
        <template #c1="{ row: item }"><span v-if="item.parsed">{{ item.segmentName }}</span><NInput v-else v-model:value.trim="item.segmentName" :name="`segment_${item.referenceKey}`" :aria-label="`${item.filename} 分段名`" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" :maxlength="80" placeholder="例如：806C" @update:value="emit('parametersChanged', item)" :input-props="{ 'name': `segment_${item.referenceKey}`, 'aria-label': `${item.filename} 分段名`, 'autocomplete': 'off' }" /></template>
        <template #c2="{ row: item }"><NInput v-model:value.trim="item.material" :name="`material_${item.referenceKey}`" :aria-label="`${item.filename} 材质`" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" :maxlength="80" placeholder="例如：A0、B0、SS" @update:value="emit('parametersChanged', item)" :input-props="{ 'name': `material_${item.referenceKey}`, 'aria-label': `${item.filename} 材质`, 'autocomplete': 'off' }" /></template>
        <template #c3="{ row: item }"><NInputNumber :value="item.thickness === '' ? null : Number(item.thickness)" @update:value="item.thickness = $event ?? ''; emit('parametersChanged', item)" :name="`thickness_${item.referenceKey}`" :aria-label="`${item.filename} 板厚`" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" inputmode="decimal" :min="0.01" :max="1000" :step="0.01" placeholder="例如：12.0…" :input-props="{ 'name': `thickness_${item.referenceKey}`, 'aria-label': `${item.filename} 板厚`, 'autocomplete': 'off', 'inputmode': 'decimal' }" /></template>
        <template #c4="{ row: item }"><NInputNumber v-model:value="item.quantity" :name="`quantity_${item.referenceKey}`" :aria-label="`${item.filename} 数量`" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" inputmode="numeric" :min="1" :max="10000" :step="1" @update:value="emit('parametersChanged', item)" :input-props="{ 'name': `quantity_${item.referenceKey}`, 'aria-label': `${item.filename} 数量`, 'autocomplete': 'off', 'inputmode': 'numeric' }" :precision="0" /></template>
        <template #c5="{ row: item }"><div class="push-batch-result"><span :class="item.status">{{ batchStatusLabel(item) }}</span></div></template>
        <template #c6="{ row: item }"><div class="push-batch-actions"><template v-if="pushBatch.resultsReady"><NButton v-if="item.status === 'failed' && isStaleFileError(item)" attr-type="button" :disabled="pushBatch.saving" @click="emit('refreshCatalog')">刷新清单重选</NButton><NButton v-else-if="item.status === 'failed' && (!item.dispatchId || item.retryable)" attr-type="button" :disabled="pushBatch.saving" @click="emit('retry', item)">{{ item.dispatchId ? '重试当前任务' : '重试创建并派发' }}</NButton><NButton v-if="item.status === 'duplicate' && item.dispatchId && (!item.statusChecked || item.retryable)" attr-type="button" :disabled="pushBatch.saving" @click="emit('retry', item)">核对并继续已有任务</NButton><NButton v-if="item.dispatchId && (item.status === 'failed' || item.status === 'duplicate')" attr-type="button" :disabled="pushBatch.saving" @click="emit('check', item)">核对状态</NButton><span v-if="item.status === 'sent'">已完成</span></template><NButton v-else class="push-batch-remove" attr-type="button" :disabled="pushBatch.saving" @click="emit('remove', item)">移除</NButton></div></template>
      </AppReportTable>
          </div>
          <div v-if="!pushBatch.resultsReady" class="push-batch-safety">
            <p>标准指令名已自动识别分段并填充材质和板厚，请在提交前逐项核对。非标准名不会猜测参数，必须手工补全。</p>
            <NCheckbox v-model:checked="pushBatch.nonStandardConfirmed" name="non_standard_confirmed" :disabled="pushBatch.saving"  v-if="nonStandardBatchCount">我已人工核对 {{ nonStandardBatchCount }} 个非标准文件名及其分段、材质、板厚和数量</NCheckbox>
            <NCheckbox v-model:checked="pushBatch.overrideConfirmed" name="standard_override_confirmed" :disabled="pushBatch.saving"  v-if="overrideBatchCount">我已人工核对 {{ overrideBatchCount }} 个与标准名不一致的材质或板厚</NCheckbox>
          </div>
        </div>

        <footer class="push-batch-footer">
          <template v-if="pushBatch.resultsReady"><span>可重试安全失败项；上传结果待核对的任务须先检查魔盒，系统不会自动重复上传。</span><NButton class="primary" attr-type="button" :disabled="pushBatch.saving" @click="emit('close')" type="primary">完成</NButton></template>
          <template v-else><div><NButton attr-type="button" :disabled="pushBatch.saving" @click="emit('close')">取消</NButton><NButton class="primary" attr-type="button" :disabled="pushBatch.saving || invalidBatchCount > 0 || !machineIp || !canPush || (nonStandardBatchCount > 0 && !pushBatch.nonStandardConfirmed) || (overrideBatchCount > 0 && !pushBatch.overrideConfirmed)" @click="emit('submit')" type="primary">{{ pushBatch.saving ? '顺序派发中…' : pushBatch.items.length === 1 ? '确认派发' : '确认并顺序派发' }}</NButton></div></template>
        </footer>
      </div>
    </NModal>
</template>

<style scoped>
.push-batch-backdrop { position:fixed; z-index:1000; inset:0; display:grid; place-items:center; padding:24px; background:rgb(var(--inverted-bg-color) / .6); }
.push-batch-dialog { width:min(1180px,100%); max-height:calc(100vh - 48px); display:flex; flex-direction:column; overflow:hidden; color:var(--text-color-1); background:rgb(var(--container-bg-color)); }
.push-batch-header { display:flex; align-items:flex-start; justify-content:space-between; gap:24px; padding:20px 24px; }
.push-batch-header h2 { margin:0; font-size:20px; }
.push-batch-header p { margin:0; color:var(--text-color-3); font-size:var(--font-auxiliary); }
.push-batch-header button { width:34px; min-height:34px; flex:0 0 34px; padding:0; line-height:1; }
.push-batch-content { min-height:0; overflow:auto; padding:20px 24px; }
.push-batch-common { display:grid; grid-template-columns:minmax(280px,1.2fr) minmax(230px,.8fr) minmax(170px,.55fr); align-items:end; gap:16px; margin-bottom:18px; padding:16px; }
.push-batch-common label > span { display:block; margin-bottom:7px; color:var(--text-color-2); font-size:var(--font-auxiliary); font-weight:700; }
.push-batch-common label > div { display:flex; gap:7px; }
.push-batch-common input,.push-batch-common .app-select { width:100%; min-width:0; }
.push-batch-common button { flex:0 0 auto; min-height:38px; padding:0 11px; }
.push-batch-summary { min-height:61px; display:flex; align-items:baseline; align-content:center; flex-wrap:wrap; gap:4px; padding:10px 13px; border-left:3px solid var(--cyan); background:rgb(var(--info-color) / .1); }
.push-batch-summary strong { color:var(--blue); font-size:22px; }
.push-batch-summary span { font-size:var(--font-auxiliary); }
.push-batch-summary small { width:100%; color:var(--text-color-3); font-size:var(--font-auxiliary); }
.push-batch-error { margin-bottom:14px; padding:10px 13px; color:rgb(var(--error-color)); border-left:3px solid rgb(var(--error-color)); background:rgb(var(--error-color) / .1); font-size:var(--font-auxiliary); }
.push-batch-progress { margin-bottom:14px; padding:11px 13px; background:rgb(var(--info-color) / .1); }
.push-batch-progress > div { display:flex; align-items:center; justify-content:space-between; gap:15px; }
.push-batch-progress strong { min-width:0; overflow:hidden; color:var(--text-color-1); text-overflow:ellipsis; white-space:nowrap; font-size:var(--font-auxiliary); }
.push-batch-progress span,.push-batch-progress small { color:var(--text-color-3); font-size:var(--font-auxiliary); }
.push-batch-progress progress { width:100%; height:7px; margin:8px 0 4px; accent-color:var(--cyan); }
.push-batch-table-wrap { overflow:auto; max-height:410px; border:1px solid var(--theme-border-color); }
.push-batch-table { width:100%; min-width:0; }
.push-batch-table :deep(.n-data-table-td) { white-space:nowrap; }
.push-batch-table th,.push-batch-table td { padding:10px; border-bottom:1px solid var(--theme-border-color); text-align:left; font-size:var(--font-auxiliary); vertical-align:middle; }
.push-batch-table th { position:sticky; z-index:1; top:0; color:var(--theme-on-primary-color); background:var(--theme-table-header-color); font-size:var(--font-auxiliary); }
.push-batch-table tr.invalid { background:rgb(var(--warning-color) / .1); }
.push-batch-table input { width:100%; min-width:82px; height:34px; padding:0 9px; }
.push-batch-file { display:block; overflow:hidden; color:var(--navy); text-overflow:ellipsis; white-space:nowrap; font-size:var(--font-auxiliary); }
.push-batch-review { color:var(--text-color-2); }
.push-batch-review.manual { color:rgb(var(--warning-color)); }
.push-batch-result { white-space:nowrap; }
.push-batch-result > span { display:inline-flex; padding:4px 7px; color:rgb(var(--warning-color)); border-radius:999px; background:rgb(var(--warning-color) / .1); white-space:nowrap; }
.push-batch-result > span.sent,.push-batch-result > span.saved { color:var(--text-color-1); background:rgb(var(--success-color) / .1); }
.push-batch-result > span.failed { color:rgb(var(--error-color)); background:rgb(var(--error-color) / .1); }
.push-batch-result > span.pushing { color:var(--text-color-1); background:rgb(var(--info-color) / .1); }
.push-batch-result > span.duplicate { color:rgb(var(--warning-color)); background:rgb(var(--warning-color) / .1); }
.push-batch-remove { min-height:30px; padding:0 8px; color:var(--text-color-2); border:1px solid var(--theme-border-color); background:rgb(var(--container-bg-color)); }
.push-batch-actions { display:flex; align-items:center; justify-content:center; gap:6px; flex-wrap:nowrap; white-space:nowrap; }
.push-batch-actions button { display:block; min-height:30px; margin:3px 0; padding:0 8px; }
.push-batch-actions button:disabled { cursor:not-allowed; }
.push-batch-actions span { color:var(--text-color-1); font-size:var(--font-auxiliary); }
.push-batch-safety { margin:14px 0 0; color:rgb(var(--success-color)); font-size:var(--font-auxiliary); line-height:1.7; }
.push-batch-safety p { margin:0; }
.push-batch-safety label { display:flex; align-items:center; gap:7px; margin-top:8px; color:rgb(var(--success-color)); font-weight:700; }
.push-batch-safety input { width:15px; height:15px; accent-color:var(--blue); }
.filename-standard { display:inline-flex; padding:3px 7px; border-radius:999px; white-space:nowrap; font-size:var(--font-auxiliary); }
.push-batch-footer { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:15px 24px; border-top:1px solid var(--theme-border-color); background:rgb(var(--info-color) / .1); }
.push-batch-footer > span { color:var(--text-color-3); font-size:var(--font-auxiliary); line-height:1.5; }
.push-batch-footer > div { display:flex; flex:0 0 auto; gap:8px; margin-left:auto; }
.push-batch-footer button { min-height:38px; padding:0 16px; white-space:nowrap; }
.push-batch-footer button:disabled,.push-batch-common button:disabled { cursor:not-allowed; }
@media (max-width:1000px) {
  .push-batch-common { grid-template-columns:1fr 1fr; }
  .push-batch-summary { grid-column:1/-1; }
}
@media (max-width:560px) {
  .push-batch-backdrop { padding:0; }
  .push-batch-dialog { width:100%; max-height:100vh; min-height:100vh; }
  .push-batch-header,.push-batch-content,.push-batch-footer { padding-left:15px; padding-right:15px; }
  .push-batch-common { grid-template-columns:1fr; gap:16px; padding:16px; }
  .push-batch-summary { grid-column:auto; }
  .push-batch-footer { align-items:stretch; flex-direction:column; }
  .push-batch-footer > div { display:grid; grid-template-columns:1fr 1fr; margin-left:0; }
  .push-batch-footer > div .primary { grid-column:1/-1; }
}
.filename-standard { color:rgb(var(--success-color)); background:rgb(var(--success-color) / .1); }
.filename-standard.warning { color:rgb(var(--warning-color)); background:rgb(var(--warning-color) / .1); }
.push-batch-backdrop { backdrop-filter:blur(5px); }
.push-batch-dialog { border:1px solid var(--theme-border-color); border-radius:14px; box-shadow:var(--theme-popover-shadow); }
.push-batch-header { color:var(--text-color-1); background:var(--theme-table-header-color); }
.push-batch-summary,.push-batch-error,.push-batch-progress { border-radius:7px; }
.push-batch-table-wrap { border-radius:8px; }
</style>
