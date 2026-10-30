<script setup lang="ts">
import type { TemplateConfig } from '../../types/designer'
defineProps<{ config: TemplateConfig }>()
</script>

<template>
  <div class="template-preview" :style="{ '--preview-primary': config.primary_color, '--preview-accent': config.accent_color, '--preview-bg': config.background_color }">
            <div class="template-preview-page"><header><small>{{ config.logo_text }}</small><strong>{{ config.title_prefix }} · 示例报表</strong></header><div class="template-preview-body"><div v-if="config.show_meta" class="template-preview-meta"><span>报告周期<br><b>2026-08</b></span><span>生成时间<br><b>当前时间</b></span></div><div class="template-preview-metrics"><span><small>完成任务</small><b>232</b></span><span><small>切割长度</small><b>9,714</b></span></div><div class="template-preview-content" :class="config.layout"><div class="fake-chart"><i v-for="height in [42,68,54,82,64,76]" :key="height" :style="{ height: `${height}%` }"></i></div><div class="fake-table"><span v-for="row in 6" :key="row"></span></div></div></div></div>
          </div>
</template>
