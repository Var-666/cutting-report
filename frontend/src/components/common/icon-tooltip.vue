<script lang="ts" setup>
import { computed, useSlots } from 'vue';
import type { PopoverPlacement } from 'naive-ui';

defineOptions({ name: 'IconTooltip' });

interface Props {
  icon?: string;
  localIcon?: string;
  desc?: string;
  placement?: PopoverPlacement;
}

const props = withDefaults(defineProps<Props>(), {
  icon: 'mdi-help-circle',
  localIcon: '',
  desc: '',
  placement: 'top'
});

const slots = useSlots();
const hasCustomTrigger = computed(() => Boolean(slots.trigger));

if (!hasCustomTrigger.value && !props.icon && !props.localIcon) {
  throw new Error('icon or localIcon is required when no custom trigger slot is provided');
}
</script>

<template>
  <NTooltip :placement="placement" :trigger="hasCustomTrigger ? 'hover' : 'click'">
    <template #trigger>
      <slot name="trigger">
        <NButton text attr-type="button" :aria-label="desc || '查看说明'">
          <SvgIcon :icon="icon" :local-icon="localIcon" aria-hidden="true" />
        </NButton>
      </slot>
    </template>
    <slot>
      <span>{{ desc }}</span>
    </slot>
  </NTooltip>
</template>
