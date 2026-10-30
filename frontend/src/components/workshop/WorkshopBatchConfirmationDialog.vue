<script setup lang="ts">
import { computed } from 'vue'
import { formatChineseDateTime as time } from '../../utils/dateDisplay'
import type { User } from '../../types/auth'
import type { WorkshopBatchConfirmation } from '../../composables/useWorkshopConfirmation'
import AppReportTable from '../AppReportTable.vue'

const props = defineProps<{ batchConfirmation: WorkshopBatchConfirmation; user: User }>()
const batchDialogOpen = computed(() => props.batchConfirmation.visible)

const emit = defineEmits<{ close: []; submit: [] }>()

function display(value: string | null | undefined) { return value || '--' }
const batchTableColumns = [
  { key: 'c0', title: "序号", width: 56, align: 'center', fixed: 'left' },
  { key: 'c1', title: "工程/船号", width: 112, align: 'left' },
  { key: 'c2', title: "分段名", width: 80, align: 'left' },
  { key: 'c3', title: "程序号", width: 160, align: 'left' },
  { key: 'c4', title: "生产时间", width: 184, align: 'left' }
] as const
</script>

<template>
  <NModal :show="batchDialogOpen" :mask-closable="!batchConfirmation.saving" :close-on-esc="!batchConfirmation.saving" @update:show="visible => { if (!visible) emit('close') }">
      <div class="confirmation-dialog batch-confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="batch-confirmation-dialog-title" tabindex="-1">
        <header><div><small>任务确认</small><h2 id="batch-confirmation-dialog-title">确认以下任务由我完成</h2></div><NButton attr-type="button" aria-label="关闭" :disabled="batchConfirmation.saving" @click="emit('close')">×</NButton></header>
        <p class="batch-confirmation-intro">请核对以下 {{ batchConfirmation.tasks.length }} 项任务。确认后，任务将登记到当前工人“{{ user.display_name }}”名下。</p>
        <div class="batch-confirmation-list">
          <AppReportTable :columns="batchTableColumns" :items="batchConfirmation.tasks" :row-key="row => row.task_id" :paginate="false">
        <template #c0="{ row, index: rowIndex }">{{ rowIndex + 1 }}</template>
        <template #c1="{ row }">{{ display(row.project_code || row.ship_no) }}</template>
        <template #c2="{ row }">{{ display(row.segment_name) }}</template>
        <template #c3="{ row }"><div :title="display(row.program_no)">{{ display(row.program_no) }}</div></template>
        <template #c4="{ row }">{{ time(row.production_time) }}</template>
      </AppReportTable>
        </div>
        <p v-if="batchConfirmation.error" class="confirmation-dialog-error" role="alert">{{ batchConfirmation.error }}</p>
        <footer><NButton attr-type="button" :disabled="batchConfirmation.saving" @click="emit('close')">返回检查</NButton><NButton class="primary" attr-type="button" :disabled="batchConfirmation.saving" @click="emit('submit')" type="primary" :loading="batchConfirmation.saving">{{ batchConfirmation.saving ? '确认中…' : `确认这 ${batchConfirmation.tasks.length} 项任务` }}</NButton></footer>
      </div>
    </NModal>
</template>
