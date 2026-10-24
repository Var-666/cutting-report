<script setup lang="ts">
import { computed, ref, watch } from 'vue';
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
      <div id="admin-device-rail" class="device-rail h-full flex-col-stretch" :class="{ 'is-collapsed': appStore.siderCollapse }" :style="{ width: (appStore.siderCollapse ? themeStore.sider.mixCollapsedWidth : themeStore.sider.mixWidth) + 'px' }" @mouseenter="setDrawerVisible(true)" @focusin="setDrawerVisible(true)">
        <GlobalLogo :show-title="false" :style="{ height: themeStore.header.height + 'px' }" />
        <SimpleScrollbar>
          <nav aria-label="切换产线设备" class="device-rail-items" :aria-busy="switching || deviceSelection.loading">
            <button v-for="item in deviceSelection.items" :key="item.id" type="button" class="device-rail-item" :class="{ 'is-active': item.id === deviceSelection.id }" :aria-label="item.name" :aria-pressed="item.id === deviceSelection.id" :title="item.name" :disabled="switching" @click="switchDevice(item.id)">
              <AppIcon name="zap" :size="22" tone="current" />
              <span v-show="!appStore.siderCollapse">{{ item.name.replace(/切割机$/, '') }}</span>
            </button>
            <span v-if="deviceSelection.loading" class="device-rail-state" role="status">加载中…</span>
            <div v-else-if="deviceSelection.error" class="device-rail-state" role="alert" :title="deviceSelection.error">加载失败<button type="button" @click="loadDevices">重试</button></div>
            <span v-else-if="deviceSelection.ready && !deviceSelection.items.length" class="device-rail-state" role="status">暂无设备</span>
          </nav>
        </SimpleScrollbar>
      </div>
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
.device-rail { flex-shrink:0; background:rgb(var(--inverted-bg-color)); color:#b6c2d2; }
.device-rail :deep(.soybean-brand) { padding:0; }
.device-rail.is-collapsed .device-rail-item { min-height:40px; }
.device-rail-items { display:flex; flex-direction:column; gap:8px; padding:8px; }
.device-rail-item { display:flex; width:100%; min-height:56px; flex-direction:column; align-items:center; justify-content:center; gap:4px; padding:8px 2px; border:0; border-radius:12px; background:transparent; color:inherit; cursor:pointer; }
.device-rail-item span { max-width:100%; overflow-wrap:anywhere; font-size:11px; line-height:16px; }
.device-rail-item:hover { background:rgb(255 255 255 / .08); color:#fff; }
.device-rail-item.is-active { background:rgb(var(--primary-color)); color:#fff; }
.device-rail-item:focus-visible, .device-rail-state button:focus-visible { outline:2px solid #fff; outline-offset:2px; }
.device-rail-item:disabled { cursor:wait; }
.device-rail-state { font-size:11px; text-align:center; overflow-wrap:anywhere; }
.device-rail-state button { display:block; margin:6px auto; border:0; border-radius:4px; background:transparent; color:inherit; cursor:pointer; }
.device-menu { border-right:1px solid var(--theme-border-color); }
.device-menu-heading { gap:4px; border-bottom:1px solid var(--theme-border-color); }
.device-menu-title { min-width:0; margin:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px; font-weight:600; }
.device-menu :deep(.n-submenu-children) { position:relative; }
.device-menu :deep(.n-submenu-children)::before { content:''; position:absolute; z-index:1; top:4px; bottom:4px; left:27px; width:1px; background:var(--theme-border-color); pointer-events:none; }
</style>
