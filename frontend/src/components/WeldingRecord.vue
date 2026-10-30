<script setup lang="ts">
import type { WeldingJobRow, WeldingPartRow } from '../types/welding'
import type { DataRow } from '../types/api'
import { computed } from 'vue'
import { weldingColumns, weldingDisplay } from '../utils/weldingReport'

const props = defineProps<{ kind: 'jobs' | 'parts'; row: WeldingJobRow | WeldingPartRow }>()
const groups = computed(() => [
  { title: props.kind === 'parts' ? '工件基础信息' : '作业基础信息', keys: ['id', 'project_no', 'project_name', 'section_no', 'part_no', 'serial_number', 'device_no', 'robot_no'] },
  { title: props.kind === 'parts' ? '累计产出与源表指标' : '工艺与作业产出', keys: ['theoretical_length_m', 'actual_length_m', 'weld_type_label', 'weld_length_m', 'working_hours', 'weld_speed', 'weld_rate', 'arc_rate', 'riser_no', 'welding_seam_no'] },
  { title: '时间、原始状态与数据核查', keys: ['start_time', 'end_time', 'state_label', 'quality_reasons'] },
].map(group => ({ ...group, fields: weldingColumns[props.kind].filter(([key]) => group.keys.includes(key)) })))
</script>

<template>
  <div class="welding-record-groups"><section v-for="group in groups" :key="group.title"><h3>{{ group.title }}</h3><dl><template v-for="[key, label] in group.fields" :key="key"><dt>{{ label }}</dt><dd>{{ weldingDisplay((row as unknown as DataRow)[key], key) }}</dd></template></dl></section></div>
</template>

<style scoped>
.welding-record-groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-bottom:16px}.welding-record-groups section{padding:16px;border:1px solid var(--theme-border-color);border-radius:8px;min-width:0}.welding-record-groups section:last-child{grid-column:1/-1}.welding-record-groups h3{font-size:14px;margin:0 0 12px}.welding-record-groups dl{display:grid;grid-template-columns:140px minmax(0,1fr);gap:10px;margin:0;font-size:12px}.welding-record-groups dt{color:var(--text-color-3)}.welding-record-groups dd{margin:0;overflow-wrap:anywhere;white-space:pre-line}@media(max-width:600px){.welding-record-groups{grid-template-columns:1fr}.welding-record-groups dl{grid-template-columns:110px minmax(0,1fr)}}
</style>
