<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { NBreadcrumb, NBreadcrumbItem, NButton, NWatermark } from 'naive-ui'
import SoybeanAdminLayout from '../vendor/soybean-admin/index.vue'
import type { LayoutMode } from '../vendor/soybean-admin/types'
import AppIcon from '../components/AppIcon.vue'
import DeviceSwitcher from '../components/DeviceSwitcher.vue'
import ThemeSettings from '../components/ThemeSettings.vue'
import { useDialogFocus } from '../composables/useDialogFocus.ts'
import { useAppStore } from '../state/app.ts'
import { useThemeStore } from '../store/modules/theme'
import { deviceSelection } from '../state/devices.ts'
import { endSession } from '../state/session'
import { GLOBAL_HEADER_MENU_ID } from '../constants/app'
import { provideMixMenuContext } from './modules/global-menu/context'
import GlobalSider from './modules/global-sider/index.vue'
import GlobalLogo from './modules/global-logo/index.vue'
import ThemeDrawer from './modules/theme-drawer/index.vue'
import ThemeHeaderTools from './modules/ThemeHeaderTools.vue'
import ThemeTabs from './modules/ThemeTabs.vue'

const appStore = useAppStore()
const GlobalMenu = defineAsyncComponent(() => import('./modules/global-menu/index.vue'))
const themeStore = useThemeStore()
const route = useRoute()
const router = useRouter()
const sider = ref(null)
const { childLevelMenus, isActiveFirstLevelMenuHasChildren } = provideMixMenuContext()
const devicePage = computed(() => route.path === '/admin/home' || route.path.startsWith('/admin/reports') || route.path.startsWith('/admin/alarms') || route.path.startsWith('/admin/welding'))
const mobileMenuOpen = computed(() => appStore.isMobile && !appStore.siderCollapse)
const breadcrumbs = computed(() => route.matched.filter((record, index, records) => record.meta.title && record.path !== records[index + 1]?.path))
const transitionName = computed(() => !appStore.reducedMotion && themeStore.page.animate ? themeStore.page.animateMode : '')
watch(() => route.fullPath, () => { if (appStore.isMobile) appStore.siderCollapse = true })
useDialogFocus(mobileMenuOpen, sider, () => { appStore.siderCollapse = true })
async function logout() { await endSession(); await router.replace('/login') }
const layoutMode = computed(() => {
  const vertical: LayoutMode = 'vertical';
  const horizontal: LayoutMode = 'horizontal';
  return themeStore.layout.mode.includes(vertical) ? vertical : horizontal;
});

const headerProps = computed(() => {
  const { mode } = themeStore.layout;

  const headerPropsConfig: Record<UnionKey.ThemeLayoutMode, App.Global.HeaderProps> = {
    vertical: {
      showLogo: false,
      showMenu: false,
      showMenuToggler: true
    },
    'vertical-mix': {
      showLogo: false,
      showMenu: false,
      showMenuToggler: true
    },
    'vertical-hybrid-header-first': {
      showLogo: !isActiveFirstLevelMenuHasChildren.value,
      showMenu: true,
      showMenuToggler: false
    },
    horizontal: {
      showLogo: true,
      showMenu: true,
      showMenuToggler: false
    },
    'top-hybrid-sidebar-first': {
      showLogo: true,
      showMenu: true,
      showMenuToggler: false
    },
    'top-hybrid-header-first': {
      showLogo: true,
      showMenu: true,
      showMenuToggler: isActiveFirstLevelMenuHasChildren.value
    }
  };

  return headerPropsConfig[mode];
});

const siderVisible = computed(() => themeStore.layout.mode !== 'horizontal');

const isVerticalMix = computed(() => themeStore.layout.mode === 'vertical-mix');

const isVerticalHybridHeaderFirst = computed(() => themeStore.layout.mode === 'vertical-hybrid-header-first');

const isTopHybridSidebarFirst = computed(() => themeStore.layout.mode === 'top-hybrid-sidebar-first');

const isTopHybridHeaderFirst = computed(() => themeStore.layout.mode === 'top-hybrid-header-first');

const siderWidth = computed(() => getSiderAndCollapsedWidth(false));

const siderCollapsedWidth = computed(() => getSiderAndCollapsedWidth(true));

function getSiderAndCollapsedWidth(isCollapsed: boolean) {
  const {
    mixChildMenuWidth,
    collapsedWidth,
    width: themeWidth,
    mixCollapsedWidth,
    mixWidth: themeMixWidth
  } = themeStore.sider;

  const width = isCollapsed ? collapsedWidth : themeWidth;
  const mixWidth = isCollapsed ? mixCollapsedWidth : themeMixWidth;

  if (appStore.isMobile) return width;

  if (isTopHybridHeaderFirst.value) {
    return isActiveFirstLevelMenuHasChildren.value ? width : 0;
  }

  if (isVerticalHybridHeaderFirst.value && !isActiveFirstLevelMenuHasChildren.value) {
    return 0;
  }

  const isMixMode = isVerticalMix.value || isTopHybridSidebarFirst.value || isVerticalHybridHeaderFirst.value;
  let finalWidth = isVerticalMix.value && !isCollapsed ? Math.max(themeMixWidth, mixChildMenuWidth) : isMixMode ? mixWidth : width;

  if (isVerticalMix.value && appStore.mixSiderFixed) {
    finalWidth += mixChildMenuWidth;
  }

  if (isVerticalHybridHeaderFirst.value && appStore.mixSiderFixed && childLevelMenus.value.length) {
    finalWidth += mixChildMenuWidth;
  }

  return finalWidth;
}
</script>
<template>
  <div class="app-shell soybean-shell">
    <a class="skip-link" href="#admin-main">跳到主要内容</a>
    <SoybeanAdminLayout v-model:sider-collapse="appStore.siderCollapse" class="soybean-layout" scroll-el-id="admin-main"
      :mode="appStore.isMobile ? 'vertical' : layoutMode" :scroll-mode="themeStore.layout.scrollMode"
      :is-mobile="appStore.isMobile" :fixed-top="themeStore.fixedHeaderAndTab"
      :header-height="appStore.isMobile ? Math.max(104, themeStore.header.height) : themeStore.header.height"
      :tab-visible="themeStore.tab.visible" :tab-height="themeStore.tab.height"
      :sider-visible="siderVisible" :sider-width="siderWidth" :sider-collapsed-width="siderCollapsedWidth"
      :footer-visible="false" content-class="min-w-0">
      <template #header>
        <div class="soybean-header">
          <GlobalLogo v-if="headerProps.showLogo && !appStore.isMobile" :show-title="false" />
          <MenuToggler v-if="headerProps.showMenuToggler || appStore.isMobile" :collapsed="appStore.siderCollapse" :aria-label="isVerticalMix && !appStore.isMobile ? (appStore.siderCollapse ? '展开设备栏' : '收起设备栏') : (appStore.siderCollapse ? '展开菜单' : '收起菜单')" :aria-expanded="!appStore.siderCollapse" :aria-controls="isVerticalMix && !appStore.isMobile ? 'admin-device-rail' : 'admin-navigation'" @click="appStore.toggleSiderCollapse" />
          <div v-if="headerProps.showMenu" :id="GLOBAL_HEADER_MENU_ID" class="h-full flex-y-center flex-1-hidden" />
          <NBreadcrumb v-else-if="themeStore.header.breadcrumb.visible" class="soybean-breadcrumb" aria-label="当前位置">
            <NBreadcrumbItem v-for="item in breadcrumbs" :key="item.path">
              <span class="flex-y-center gap-6px"><AppIcon v-if="themeStore.header.breadcrumb.showIcon && item.meta.icon" :name="item.meta.icon" :size="18" tone="current" />{{ item.meta.title }}</span>
            </NBreadcrumbItem>
          </NBreadcrumb>
          <div class="soybean-header-actions">
            <ThemeHeaderTools />
            <ThemeSettings />
            <DeviceSwitcher v-if="!isVerticalMix || appStore.isMobile" />
            <NButton quaternary class="soybean-logout" aria-label="退出登录" @click="logout"><template #icon><AppIcon name="logout" :size="20" tone="current" /></template><span class="lt-sm:hidden">退出登录</span></NButton>
          </div>
        </div>
      </template>
      <template #sider>
        <div id="admin-navigation" ref="sider" class="h-full" :inert="appStore.isMobile && appStore.siderCollapse" :aria-hidden="appStore.isMobile && appStore.siderCollapse || undefined" :role="mobileMenuOpen ? 'dialog' : undefined" :aria-modal="mobileMenuOpen ? true : undefined" aria-label="管理员导航" tabindex="-1"><GlobalSider /></div>
      </template>
      <template #tab><ThemeTabs /></template>
      <div class="admin-main soybean-page">
        <RouterView v-if="!devicePage || deviceSelection.ready" v-slot="{ Component }" :key="devicePage ? deviceSelection.id : undefined">
          <Transition :name="transitionName" mode="out-in"><div :key="route.matched[1]?.path" class="min-w-0"><component :is="Component" /></div></Transition>
        </RouterView>
      </div>
      <GlobalMenu />
      <ThemeDrawer />
    </SoybeanAdminLayout>
    <NWatermark v-if="themeStore.watermark.visible" :content="themeStore.watermarkContent" cross fullscreen :font-size="16" :line-height="16" :width="384" :height="384" :x-offset="12" :y-offset="60" :rotate="-15" />
  </div>
</template>
