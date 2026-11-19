<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NPopover, NTag } from 'naive-ui'
import type { EmailDelivery } from '../../types/automation'

const props = defineProps<{ delivery: Pick<EmailDelivery, 'recipients' | 'recipient_results'>; compact?: boolean }>()
const labels: Record<string, string> = { accepted: '服务器已接受', unknown: '结果待核对', not_accepted: '服务器未接收（人工核对）', refused: '服务器拒收' }
const tones: Record<string, 'success' | 'warning' | 'error' | undefined> = { accepted: 'success', unknown: 'warning', not_accepted: 'error', refused: 'error' }
const recipients = computed(() => [...new Set([...props.delivery.recipients, ...Object.keys(props.delivery.recipient_results || {})])].map(address => {
  const result = props.delivery.recipient_results?.[address]
  const status = result?.status
  const detail = status ? `${labels[status] || status}${result?.code ? ` ${result.code}` : ''}` : ''
  const type: 'success' | 'warning' | 'error' | 'default' = tones[status || ''] ?? 'default'
  return { address, detail, title: detail ? `${address} · ${detail}` : address, type }
}))
</script>

<template>
  <div class="recipient-list" :class="{ 'recipient-list--compact': compact }" aria-label="收件人">
    <NTag v-for="item in (compact ? recipients.slice(0, 1) : recipients)" :key="item.address" size="small" :type="item.type"
      :title="item.title" :aria-label="item.title">
      <span class="recipient-address">{{ item.address }}</span><span v-if="!compact && item.detail" class="recipient-result">{{ item.detail }}</span>
    </NTag>
    <NPopover v-if="compact && recipients.length > 1" trigger="click" placement="bottom-start"
      style="width:min(520px, calc(100vw - 32px)); max-height:50vh; overflow:auto">
      <template #trigger><NButton size="tiny" quaternary :aria-label="`查看全部 ${recipients.length} 位收件人`">+{{ recipients.length - 1 }}</NButton></template>
      <AutomationRecipientTags :delivery="delivery" />
    </NPopover>
    <span v-if="!recipients.length">—</span>
  </div>
</template>

<style scoped>
.recipient-list { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-1); min-width:0; }
.recipient-list--compact { flex-wrap:nowrap; white-space:nowrap; }
.recipient-list :deep(.n-tag) { min-width:0; max-width:100%; }
.recipient-list :deep(.n-tag__content) { display:flex; gap:var(--space-1); min-width:0; }
.recipient-address { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.recipient-result { flex:none; font-size:var(--font-auxiliary); }
.recipient-list > button { flex:none; }
</style>
