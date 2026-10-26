<script setup lang="ts">
import { computed, h, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { useBoolean } from '@sa/hooks';
import { SimpleScrollbar } from '@sa/materials';
import { GLOBAL_SIDER_MENU_ID } from '@/constants/app';
import { useAppStore } from '@/store/modules/app';
import { useRouteStore } from '@/store/modules/route';
import { useThemeStore } from '@/store/modules/theme';
import { useRouterPush } from '@/hooks/common/router';
import { useMenu } from '../context';
import AppIcon from '@/components/AppIcon.vue';
import { useDeviceSelection } from '@/composables/useDeviceSelection.js';
import GlobalLogo from '../../global-logo/index.vue';
import { productionGroups } from '@/utils/productionLines.js';

defineOptions({
  name: 'VerticalMixMenu'
});

const route = useRoute();
const appStore = useAppStore();
const themeStore = useThemeStore();
const routeStore = useRouteStore();
const { routerPushByKeyWithMetaQuery } = useRouterPush();
const { bool: drawerVisible, setBool: setDrawerVisible } = useBoolean();
const { deviceSelection, switching, switchDevice, loadDevices } = useDeviceSelection();
const { selectedKey } = useMenu();

const activeDevice = computed(() => deviceSelection.items.find(item => item.id === deviceSelection.id));
const deviceMenus = computed(() => productionGroups(deviceSelection.items).map(group => ({
  key: `type:${group.id}`,
  label: group.name,
  icon: () => h(AppIcon, { name: group.id === 'cutting' ? 'zap' : 'factory', size: 20, tone: 'current' }),
  children: group.items.map(item => ({
    key: item.id,
    label: item.name,
    disabled: switching.value,
    icon: () => h(AppIcon, { name: group.id === 'cutting' ? 'zap' : 'factory', size: 20, tone: 'current' })
  }))
})));
const expandedTypes = ref(['type:cutting', 'type:welding']);

const showDrawer = computed(() => drawerVisible.value || appStore.mixSiderFixed);

function handleResetActiveMenu() {
  setDrawerVisible(false);
}

const expandedKeys = ref<string[]>([]);

function updateExpandedKeys() {
  if (!selectedKey.value) {
    expandedKeys.value = [];
    return;
  }
  expandedKeys.value = routeStore.getSelectedMenuKeyPath(selectedKey.value);
}

watch(
  () => route.name,
  () => {
    updateExpandedKeys();
  },
  { immediate: true }
);
</script>

<template>
  <Teleport :to="`#${GLOBAL_SIDER_MENU_ID}`">
    <div class="vertical-mix-navigation h-full flex" @mouseleave="handleResetActiveMenu" @focusout="event => { if (!event.currentTarget.contains(event.relatedTarget)) handleResetActiveMenu() }">
      <DarkModeContainer id="admin-device-rail" class="device-rail h-full flex-col-stretch" :style="{ width: (appStore.siderCollapse ? themeStore.sider.mixCollapsedWidth : Math.max(themeStore.sider.mixWidth, themeStore.sider.mixChildMenuWidth)) + 'px' }" @mouseenter="setDrawerVisible(true)" @focusin="setDrawerVisible(true)">
        <GlobalLogo :show-title="false" :style="{ height: themeStore.header.height + 'px' }" />
        <SimpleScrollbar>
          <nav aria-label="切换产线设备" :aria-busy="switching || deviceSelection.loading">
            <NMenu
              v-model:expanded-keys="expandedTypes"
              mode="vertical"
              :value="deviceSelection.id"
              :options="deviceMenus"
              :collapsed="appStore.siderCollapse"
              :collapsed-width="themeStore.sider.mixCollapsedWidth"
              :collapsed-icon-size="20"
              :indent="18"
              @update:value="switchDevice"
            />
            <span v-if="deviceSelection.loading" class="device-rail-state" role="status">加载中…</span>
            <div v-else-if="deviceSelection.error" class="device-rail-state" role="alert" :title="deviceSelection.error">加载失败<button type="button" @click="loadDevices">重试</button></div>
            <span v-else-if="deviceSelection.ready && !deviceSelection.items.length" class="device-rail-state" role="status">暂无设备</span>
          </nav>
        </SimpleScrollbar>
      </DarkModeContainer>
      <div
        class="relative h-full transition-width-300"
        :style="{ width: appStore.mixSiderFixed ? themeStore.sider.mixChildMenuWidth + 'px' : '0px' }"
      >
        <DarkModeContainer
          class="device-menu absolute-lt h-full flex-col-stretch nowrap-hidden shadow-sm transition-all-300"
          :style="{ width: showDrawer ? themeStore.sider.mixChildMenuWidth + 'px' : '0px' }"
          :inert="!showDrawer"
          :aria-hidden="!showDrawer || undefined"
        >
          <header class="device-menu-heading flex-y-center justify-between px-12px" :style="{ height: themeStore.header.height + 'px' }">
            <h2 class="device-menu-title" :title="activeDevice?.name">{{ activeDevice?.name || '产线设备' }}</h2>
            <PinToggler
              :pin="appStore.mixSiderFixed"
              @click="appStore.toggleMixSiderFixed"
            />
          </header>
          <SimpleScrollbar>
            <NMenu
              v-model:expanded-keys="expandedKeys"
              mode="vertical"
              :value="selectedKey"
              :options="routeStore.menus"
              :indent="18"
              @update:value="routerPushByKeyWithMetaQuery"
            />
          </SimpleScrollbar>
        </DarkModeContainer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.device-rail { flex-shrink:0; border-right:1px solid var(--theme-border-color); }
.device-rail :deep(.soybean-brand) { padding:0; }
.device-rail-state button:focus-visible { outline:2px solid rgb(var(--primary-color)); outline-offset:2px; }
.device-rail-state { font-size:11px; text-align:center; overflow-wrap:anywhere; }
.device-rail-state button { display:block; margin:6px auto; border:0; border-radius:4px; background:transparent; color:inherit; cursor:pointer; }
.device-menu { border-right:1px solid var(--theme-border-color); }
.device-menu-heading { gap:4px; border-bottom:1px solid var(--theme-border-color); }
.device-menu-title { min-width:0; margin:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px; font-weight:600; }
:is(.device-rail,.device-menu) :deep(.n-submenu-children) { position:relative; }
:is(.device-rail,.device-menu) :deep(.n-submenu-children)::before { content:''; position:absolute; z-index:1; top:4px; bottom:4px; left:27px; width:1px; background:var(--theme-border-color); pointer-events:none; }
</style>
