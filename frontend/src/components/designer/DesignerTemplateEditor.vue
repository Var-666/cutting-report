<script setup lang="ts">
import type { ReportTemplate } from '../../types/designer'
import DesignerTemplatePreview from './DesignerTemplatePreview.vue'

const templateDraft = defineModel<ReportTemplate | null>({ required: true })
defineProps<{ saving: boolean }>()
const emit = defineEmits<{ save: [draft: ReportTemplate]; remove: [draft: ReportTemplate]; copy: [draft: ReportTemplate] }>()


</script>

<template>
  <div v-if="templateDraft" class="designer-editor template-editor">
        <div class="designer-editor-head"><div><span>TEMPLATE DESIGNER</span><h3>{{ templateDraft.template_id ? templateDraft.name : '新建报表模板' }}</h3></div><button v-if="templateDraft.is_builtin" type="button" @click="emit('copy', templateDraft)">复制为新模板</button></div>
        <div class="template-design-grid">
          <div>
            <div class="designer-form-grid">
              <label><span>模板名称</span><input v-model="templateDraft.name" name="template_name" autocomplete="off" :disabled="templateDraft.is_builtin" /></label>
              <label><span>模板编码</span><input v-model="templateDraft.code" name="template_code" autocomplete="off" spellcheck="false" :disabled="templateDraft.is_builtin || !!templateDraft.template_id" /></label>
              <label class="wide"><span>模板说明</span><input v-model="templateDraft.description" name="template_description" autocomplete="off" :disabled="templateDraft.is_builtin" /></label>
              <label><span>报告标题前缀</span><input v-model="templateDraft.config.title_prefix" name="template_title_prefix" autocomplete="off" :disabled="templateDraft.is_builtin" /></label>
              <label><span>页眉标识</span><input v-model="templateDraft.config.logo_text" name="template_logo_text" autocomplete="off" :disabled="templateDraft.is_builtin" /></label>
              <label><span>主色</span><div class="color-field"><input v-model="templateDraft.config.primary_color" name="template_primary_color" type="color" :disabled="templateDraft.is_builtin" /><code>{{ templateDraft.config.primary_color }}</code></div></label>
              <label><span>强调色</span><div class="color-field"><input v-model="templateDraft.config.accent_color" name="template_accent_color" type="color" :disabled="templateDraft.is_builtin" /><code>{{ templateDraft.config.accent_color }}</code></div></label>
              <label><span>页面背景</span><div class="color-field"><input v-model="templateDraft.config.background_color" name="template_background_color" type="color" :disabled="templateDraft.is_builtin" /><code>{{ templateDraft.config.background_color }}</code></div></label>
              <label><span>内容布局</span><AppSelect v-model="templateDraft.config.layout" :disabled="templateDraft.is_builtin"><option value="split">图表与表格双栏</option><option value="chart_first">图表在前</option><option value="table_first">表格在前</option></AppSelect></label>
              <label><span>表格密度</span><AppSelect v-model="templateDraft.config.table_density" :disabled="templateDraft.is_builtin"><option value="compact">紧凑</option><option value="normal">标准</option><option value="comfortable">宽松</option></AppSelect></label>
              <label><span>图表高度 {{ templateDraft.config.chart_height }}px</span><input v-model.number="templateDraft.config.chart_height" name="template_chart_height" type="range" min="220" max="520" step="10" :disabled="templateDraft.is_builtin" /></label>
            </div>
            <div class="designer-actions"><label class="switch-label"><input v-model="templateDraft.config.show_meta" name="template_show_meta" type="checkbox" :disabled="templateDraft.is_builtin" />显示报告元信息</label><label class="switch-label"><input v-model="templateDraft.enabled" name="template_enabled" type="checkbox" :disabled="templateDraft.is_builtin" />启用</label><button class="primary" type="button" :disabled="saving || templateDraft.is_builtin" @click="emit('save', templateDraft)">保存模板</button><button class="danger-button" type="button" :disabled="templateDraft.is_builtin" @click="emit('remove', templateDraft)">删除</button></div>
          </div>
          <DesignerTemplatePreview :config="templateDraft.config" />
        </div>
      </div>
</template>
